import asyncio
import base64
import copy
import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kira.tools import browser_auth
from kira.tools.browser_auth import (
    AuthRequestError,
    BrowserAuthBridge,
    normalize_auth_request,
    validate_auth_response,
)


def options(kind="create"):
    result = {
        "challenge": "AQIDBA",
        "timeout": 180000,
        "extensions": {"remoteDesktopClientOverride": {
            "origin": "https://login.example.com", "sameOriginWithAncestors": True}},
    }
    if kind == "create":
        result.update(rp={"id": "example.com", "name": "Synthetic example"},
                      user={"id": "AQID", "name": "Synthetic", "displayName": "Synthetic"},
                      pubKeyCredParams=[{"type": "public-key", "alg": -7},
                                        {"type": "public-key", "alg": -257}])
    else:
        result.update(rpId="example.com", userVerification="required")
    return result


def get_response(request, *, credential_id="AQID", authenticator_data=None):
    if authenticator_data is None:
        authenticator_data = hashlib.sha256(request["rpId"].encode()).digest() + b"\x05" + bytes(4)
    return {"id": credential_id, "rawId": credential_id, "type": "public-key",
            "clientExtensionResults": {}, "response": {"clientDataJSON": request["clientData"],
            "authenticatorData": encoded(authenticator_data),
            "signature": encoded(b"\x30\x06\x02\x01\x01\x02\x01\x01"), "userHandle": "AQID"}}


def test_request_conversion_preserves_authenticator_preferences_and_order():
    source = options()
    source["authenticatorSelection"] = {"authenticatorAttachment": "platform",
        "residentKey": "preferred", "userVerification": "required"}
    source["attestation"] = "direct"
    source["extensions"]["credProps"] = True
    source["excludeCredentials"] = [
        {"type": "public-key", "id": "AQIDBA", "transports": ["internal", "usb", "hybrid"]}]
    before = copy.deepcopy(source)
    result = normalize_auth_request("create", source, 9)
    assert source == before
    assert result["algorithms"] == [-7, -257]
    assert result["attachment"] == 1
    assert result["requireResidentKey"] is False and result["preferResidentKey"] is True
    assert result["userVerification"] == 1 and result["attestation"] == 3
    assert result["credentials"] == [{"id": "AQIDBA", "transports": 49}]
    assert result["credProps"] is True
    data = json.loads(base64.urlsafe_b64decode(result["clientData"] + "=="))
    assert data == {"type": "webauthn.create", "challenge": "AQIDBA",
                    "origin": "https://login.example.com", "crossOrigin": False}


@pytest.mark.parametrize("resident,required,prefer", [
    ("required", True, False), ("preferred", False, True), ("discouraged", False, False)])
def test_resident_choices_are_distinct(resident, required, prefer):
    source = options()
    source["authenticatorSelection"] = {"residentKey": resident}
    normalized = normalize_auth_request("create", source, 3)
    assert normalized["requireResidentKey"] is required
    assert normalized["preferResidentKey"] is prefer


def test_get_preserves_allow_list_and_verification():
    source = options("get")
    source["allowCredentials"] = [{"type": "public-key", "id": "AQID", "transports": ["nfc"]}]
    result = normalize_auth_request("get", source, 9)
    assert result["credentials"] == [{"id": "AQID", "transports": 2}]
    assert result["userVerification"] == 1
    assert result["rpId"] == "example.com"
    assert "userId" not in result


@pytest.mark.parametrize("extension", ["prf", "appid", "largeBlob", "credBlob", "hmacCreateSecret"])
def test_unsupported_extensions_are_not_silently_removed(extension):
    source = options()
    source["extensions"][extension] = {"synthetic": "secret must not appear in error"}
    with pytest.raises(AuthRequestError) as error:
        normalize_auth_request("create", source, 9)
    assert error.value.name == "NotSupportedError"
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("change", [
    lambda value: value.pop("extensions"),
    lambda value: value["extensions"]["remoteDesktopClientOverride"].update(
        sameOriginWithAncestors=False),
    lambda value: value["extensions"]["remoteDesktopClientOverride"].update(origin="http://example.com"),
    lambda value: value["extensions"]["remoteDesktopClientOverride"].update(origin="https://example.com/path"),
    lambda value: value["extensions"]["remoteDesktopClientOverride"].update(origin="https://user@example.com"),
    lambda value: value["rp"].update(id="unrelated.example"),
    lambda value: value.update(challenge="AQID="),
    lambda value: value.update(challenge="a"),
    lambda value: value.update(timeout=True),
    lambda value: value.update(timeout=300001),
    lambda value: value.update(hints=["client-device"]),
    lambda value: value.update(attestation="enterprise"),
    lambda value: value.update(attestationFormats=["packed"]),
    lambda value: value["pubKeyCredParams"][0].update(alg=True),
    lambda value: value["user"].update(id="A" * 1000),
    lambda value: value.update(excludeCredentials=[{"type": "password", "id": "AQID"}]),
])
def test_malformed_or_unsupported_options_fail_before_native_effects(change):
    source = options()
    change(source)
    with pytest.raises(AuthRequestError):
        normalize_auth_request("create", source, 9)


def test_hybrid_requires_supported_windows_version():
    source = options("get")
    source["allowCredentials"] = [{"type": "public-key", "id": "AQID", "transports": ["hybrid"]}]
    with pytest.raises(AuthRequestError):
        normalize_auth_request("get", source, 3)


class FakeWorker:
    url = "chrome-extension://owned/worker.js"

    def __init__(self):
        self.events = asyncio.Queue()
        self.completed = []
        self.detached = False

    async def evaluate(self, script, value=None):
        if script.endswith(".next()"):
            return await self.events.get()
        if script.endswith(".detach()"):
            self.detached = True
        if value is not None:
            self.completed.append(value)

    def event(self, kind, request_id=-17, data=None):
        detail = {"requestId": request_id}
        if kind in {"create", "get"}:
            detail["requestDetailsJson"] = json.dumps(data or options(kind))
        self.events.put_nowait({"kind": kind, "detail": detail})


class FakeHelper:
    def __init__(self):
        self.requests = []
        self.done = asyncio.Event()
        self.response = {"ok": False, "error": {"name": "NotAllowedError"}}
        self.cancelled = False
        self.closed = False

    async def perform(self, request):
        self.requests.append(request)
        await self.done.wait()
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def cancel(self):
        self.cancelled = True
        self.done.set()

    async def close(self):
        self.closed = True
        self.done.set()


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


async def setup_bridge(manual=False):
    state = {"manual": manual}
    reports = []

    async def report(value):
        reports.append(value)

    bridge = BrowserAuthBridge(report, lambda: state["manual"])
    helper, worker = FakeHelper(), FakeWorker()
    bridge._helper = helper
    bridge._api_version = 9
    bridge._extension_origin = "chrome-extension://owned"
    bridge.capabilities = {"windows_webauthn": True, "platform_authenticator": True}
    await bridge.attach(SimpleNamespace(service_workers=[worker]))
    if manual:
        await bridge.set_manual(True)
    return bridge, helper, worker, state, reports


async def test_agent_request_waits_for_explicit_handoff_and_metadata_is_private():
    bridge, helper, worker, state, reports = await setup_bridge()
    try:
        worker.event("create")
        await until(lambda: reports)
        assert not helper.requests
        assert reports[-1] == {"operation": "create", "origin": "https://login.example.com",
                               "result": "needs_manual", "pending": True}
        # The separate consent callback must also agree; pause alone is insufficient.
        await bridge.set_manual(True)
        await asyncio.sleep(.01)
        assert not helper.requests
        state["manual"] = True
        await bridge.set_manual(True)
        await until(lambda: helper.requests)
        helper.done.set()
        await until(lambda: worker.completed)
        assert worker.completed[0]["requestId"] == -17
        assert worker.completed[0]["error"]["name"] == "NotAllowedError"
        assert all(set(value) == {"operation", "origin", "result", "pending"} for value in reports)
        assert "Synthetic" not in json.dumps(reports) and "AQID" not in json.dumps(reports)
    finally:
        await bridge.close()


async def test_unsupported_request_never_starts_helper():
    bridge, helper, worker, _, reports = await setup_bridge(True)
    try:
        source = options()
        source["extensions"]["prf"] = {"eval": {"first": "AQIDBA"}}
        worker.event("create", data=source)
        await until(lambda: worker.completed)
        assert not helper.requests
        assert worker.completed[0]["error"]["name"] == "NotSupportedError"
        assert reports[-1]["result"] == "unsupported"
        assert reports[-1]["origin"] == "https://login.example.com"
    finally:
        await bridge.close()


async def test_cancel_before_handoff_does_not_start_authentication():
    bridge, helper, worker, _, reports = await setup_bridge()
    try:
        worker.event("get")
        await until(lambda: reports)
        worker.event("cancel")
        await until(lambda: reports[-1]["pending"] is False)
        assert not helper.requests
        assert reports[-1]["result"] == "cancelled"
    finally:
        await bridge.close()


async def test_resume_cancels_native_request_without_claiming_success():
    bridge, helper, worker, state, reports = await setup_bridge(True)
    try:
        worker.event("create")
        await until(lambda: helper.requests)
        state["manual"] = False
        await bridge.set_manual(False)
        await until(lambda: reports[-1]["pending"] is False)
        assert helper.cancelled
        assert reports[-1]["result"] == "unconfirmed"
        assert worker.completed[-1]["error"]["name"] == "AbortError"
    finally:
        await bridge.close()


async def test_helper_disconnect_is_unconfirmed_and_cannot_confirm_to_page():
    bridge, helper, worker, _, reports = await setup_bridge(True)
    try:
        helper.response = RuntimeError("Synthetic private helper bytes must remain private")
        helper.done.set()
        worker.event("get")
        await until(lambda: worker.completed)
        assert reports[-1]["result"] == "unconfirmed"
        assert helper.closed
        assert "private helper bytes" not in json.dumps(reports + worker.completed)
    finally:
        await bridge.close()


async def test_successful_mock_result_is_returned_once_without_leaking_body():
    bridge, helper, worker, _, reports = await setup_bridge(True)
    try:
        response = get_response(normalize_auth_request("get", options("get"), 9))
        helper.response = {"ok": True, "response": response}
        helper.done.set()
        worker.event("get")
        await until(lambda: worker.completed)
        assert json.loads(worker.completed[0]["responseJson"]) == response
        assert reports[-1]["result"] == "completed"
        assert "AQID" not in json.dumps(reports)
        assert len(helper.requests) == 1
    finally:
        await bridge.close()


async def test_only_owned_extension_worker_is_attached():
    bridge = BrowserAuthBridge(AsyncMock(), lambda: False)
    helper = FakeHelper()
    bridge._helper = helper
    bridge._extension_origin = "chrome-extension://owned"
    unrelated = SimpleNamespace(url="chrome-extension://unrelated/worker.js")
    context = SimpleNamespace(service_workers=[unrelated],
                              wait_for_event=AsyncMock(side_effect=TimeoutError))
    assert await bridge.attach(context) is False
    assert helper.closed
    predicate = context.wait_for_event.call_args.kwargs["predicate"]
    assert predicate(unrelated) is False


async def test_prepare_is_availability_only_and_launch_arguments_are_narrow(monkeypatch, tmp_path):
    helper = SimpleNamespace(start=AsyncMock(return_value={"type": "availability", "ok": True,
        "apiVersion": 9, "platformAuthenticatorAvailable": True}), close=AsyncMock())
    monkeypatch.setattr(browser_auth, "_powershell", lambda: "/synthetic/powershell.exe")
    monkeypatch.setattr(browser_auth, "_WindowsHelper", lambda executable: helper)
    bridge = BrowserAuthBridge(AsyncMock(), lambda: False)
    try:
        assert await bridge.prepare(tmp_path)
        assert bridge.ignore_default_args == ["--disable-extensions"]
        assert len(bridge.launch_args) == 2
        assert all("auth_extension" in value for value in bridge.launch_args)
        assert bridge.capabilities["platform_authenticator"] is True
        assert bridge._extension_origin.startswith("chrome-extension://")
    finally:
        await bridge.close()


def test_extension_does_not_request_page_or_global_native_messaging_access():
    manifest = json.loads((Path(browser_auth.__file__).parent / "auth_extension/manifest.json").read_text())
    assert manifest["permissions"] == ["webAuthenticationProxy"]
    assert not {"host_permissions", "content_scripts", "externally_connectable"} & manifest.keys()


@pytest.mark.parametrize("change", [
    lambda value: value.update(id="BAQE"),
    lambda value: value["response"].update(clientDataJSON="AQID"),
    lambda value: value["response"].update(signature="invalid encoding !"),
    lambda value: value.update(clientExtensionResults={"prf": {"results": {}}}),
    lambda value: value.update(authenticatorAttachment="unexpected"),
])
def test_native_response_is_bound_to_original_client_data(change):
    request = normalize_auth_request("get", options("get"), 9)
    response = get_response(request)
    change(response)
    with pytest.raises(AuthRequestError) as error:
        validate_auth_response("get", response, request)
    assert error.value.name == "OperationError"


async def test_manual_wait_timeout_does_not_start_native_operation(monkeypatch):
    monkeypatch.setattr(browser_auth, "MAX_MANUAL_WAIT", .01)
    bridge, helper, worker, _, reports = await setup_bridge()
    try:
        worker.event("get")
        await until(lambda: worker.completed)
        assert not helper.requests
        assert reports[-1]["pending"] is False
    finally:
        await bridge.close()


async def test_uvpaa_has_no_authentication_effect_even_while_agent_controls():
    bridge, helper, worker, _, reports = await setup_bridge()
    try:
        worker.event("uvpaa", request_id=-2147483648)
        await until(lambda: worker.completed)
        assert worker.completed == [{"kind": "uvpaa", "requestId": -2147483648, "isUvpaa": True}]
        assert not helper.requests and not reports
    finally:
        await bridge.close()


async def test_second_request_is_rejected_while_first_waits_for_human():
    bridge, helper, worker, _, reports = await setup_bridge()
    try:
        worker.event("get", request_id=-1)
        await until(lambda: reports)
        worker.event("create", request_id=2)
        await until(lambda: worker.completed)
        assert worker.completed[0]["requestId"] == 2
        assert worker.completed[0]["error"]["name"] == "NotAllowedError"
        assert bridge._pending_id == -1 and not helper.requests
    finally:
        await bridge.close()


async def test_close_cancels_pending_native_work_without_replay_or_late_success():
    bridge, helper, worker, _, reports = await setup_bridge(True)
    worker.event("create")
    await until(lambda: helper.requests)
    await bridge.close()
    assert helper.closed and helper.cancelled and worker.detached
    assert len(helper.requests) == 1
    assert not any("responseJson" in value for value in worker.completed)
    assert reports[-1]["result"] == "unconfirmed"
    assert bridge._pending.done()


async def test_invalid_extension_event_fails_closed():
    bridge, helper, worker, _, reports = await setup_bridge(True)
    try:
        worker.event("get")
        await until(lambda: helper.requests)
        worker.events.put_nowait({"kind": "create", "detail": {"requestId": True}})
        await until(lambda: not bridge.capabilities["windows_webauthn"])
        assert helper.cancelled and worker.detached
        assert any(value["result"] == "unavailable" for value in reports)
    finally:
        await bridge.close()


async def test_native_helper_uses_private_pipes_and_fixed_command(monkeypatch):
    class Stream:
        def __init__(self):
            self.written = []
            self.lines = asyncio.Queue()
            self.closed = False

        def write(self, value):
            self.written.append(value)

        async def drain(self):
            pass

        async def readline(self):
            return await self.lines.get()

        def close(self):
            self.closed = True

    stdin, stdout = Stream(), Stream()
    process = SimpleNamespace(stdin=stdin, stdout=stdout, returncode=None)

    async def wait():
        process.returncode = 0
        return 0

    process.wait = wait
    create = AsyncMock(return_value=process)
    monkeypatch.setattr(browser_auth.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(browser_auth.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    stdout.lines.put_nowait(b'{"type":"availability","ok":true,"apiVersion":9}\n')
    helper = browser_auth._WindowsHelper("/synthetic/powershell.exe")
    try:
        assert (await helper.start())["apiVersion"] == 9
        stdout.lines.put_nowait(json.dumps({"type": "result", "nonce": "a" * 32,
            "ok": False, "error": {"name": "NotAllowedError"}}).encode() + b"\n")
        request = normalize_auth_request("create", options(), 9)
        await helper.perform(request)
        assert "Synthetic" not in str(create.call_args.args)
        assert b"Synthetic" in stdin.written[1]
        assert create.call_args.kwargs["stderr"] == asyncio.subprocess.DEVNULL
    finally:
        await helper.close()
    assert stdin.closed and process.returncode == 0
    assert json.loads(stdin.written[-1]) == {"command": "close"}


def cbor(value):
    def header(major, size):
        if size < 24:
            return bytes([major * 32 + size])
        if size < 256:
            return bytes([major * 32 + 24, size])
        if size < 65536:
            return bytes([major * 32 + 25]) + struct.pack(">H", size)
        return bytes([major * 32 + 26]) + struct.pack(">I", size)
    if value is None:
        return b"\xf6"
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if isinstance(value, int):
        return header(0, value) if value >= 0 else header(1, -1 - value)
    if isinstance(value, bytes):
        return header(2, len(value)) + value
    if isinstance(value, str):
        text = value.encode()
        return header(3, len(text)) + text
    if isinstance(value, list):
        return header(4, len(value)) + b"".join(cbor(item) for item in value)
    return header(5, len(value)) + b"".join(cbor(key) + cbor(item) for key, item in value.items())


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@pytest.mark.parametrize("kind", ["P256", "P384", "P521", "RSA", "Ed25519"])
def test_create_response_derives_actual_algorithm_and_spki_from_native_cose(kind):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

    if kind == "RSA":
        public = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        numbers = public.public_numbers()
        algorithm = -257
        cose = {1: 3, 3: algorithm, -1: numbers.n.to_bytes(256), -2: numbers.e.to_bytes(3)}
    elif kind == "Ed25519":
        public = ed25519.Ed25519PrivateKey.generate().public_key()
        algorithm = -8
        cose = {1: 1, 3: algorithm, -1: 6, -2: public.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)}
    else:
        curve, identifier, algorithm, size = {
            "P256": (ec.SECP256R1(), 1, -7, 32),
            "P384": (ec.SECP384R1(), 2, -35, 48),
            "P521": (ec.SECP521R1(), 3, -36, 66),
        }[kind]
        public = ec.generate_private_key(curve).public_key()
        numbers = public.public_numbers()
        cose = {1: 2, 3: algorithm, -1: identifier,
                -2: numbers.x.to_bytes(size), -3: numbers.y.to_bytes(size)}
    request = normalize_auth_request("create", options(), 9)
    request["algorithms"] = [-7 if algorithm != -7 else -257, algorithm]
    auth_data = hashlib.sha256(request["rpId"].encode()).digest() + b"\x45" + bytes(4 + 16) + b"\x00\x03\x01\x02\x03" + cbor(cose)
    response = {"id": "AQID", "rawId": "AQID", "type": "public-key",
                "clientExtensionResults": {}, "response": {"clientDataJSON": request["clientData"],
                "attestationObject": encoded(cbor({"fmt": "none", "authData": auth_data, "attStmt": {}})),
                "authenticatorData": encoded(auth_data),
                "transports": ["internal"]}}
    converted = json.loads(validate_auth_response("create", response, request))
    assert converted["response"]["publicKeyAlgorithm"] == algorithm
    expected = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert converted["response"]["publicKey"] == encoded(expected)
    assert "publicKey" not in response["response"]


@pytest.mark.parametrize("change", [
    lambda data: b"short",
    lambda data: bytes(32) + data[32:],
    lambda data: data[:32] + b"\x04" + data[33:],  # no user presence
    lambda data: data[:32] + b"\x01" + data[33:],  # required verification absent
    lambda data: data[:32] + b"\x15" + data[33:],  # backup state without eligibility
    lambda data: data[:32] + b"\x45" + data[33:],  # assertion cannot include attested data
    lambda data: data + b"unexpected tail",
    lambda data: data[:32] + b"\x85" + data[33:],  # extension flag without a CBOR map
])
def test_assertion_rejects_wrong_rp_flags_truncation_and_extra_data(change):
    request = normalize_auth_request("get", options("get"), 9)
    data = hashlib.sha256(request["rpId"].encode()).digest() + b"\x05" + bytes(4)
    with pytest.raises(AuthRequestError) as error:
        validate_auth_response("get", get_response(request, authenticator_data=change(data)), request)
    assert error.value.name == "OperationError"


def test_assertion_allow_list_and_optional_verification_are_preserved():
    source = options("get")
    source.update(userVerification="preferred", allowCredentials=[{"type": "public-key", "id": "AQID"}])
    request = normalize_auth_request("get", source, 9)
    data = hashlib.sha256(request["rpId"].encode()).digest() + b"\x01" + bytes(4)
    response = get_response(request, authenticator_data=data)
    assert json.loads(validate_auth_response("get", response, request))["rawId"] == "AQID"
    with pytest.raises(AuthRequestError):
        validate_auth_response("get", get_response(request, credential_id="BAQE", authenticator_data=data), request)
    extended = data[:32] + b"\x81" + data[33:] + cbor({"example": True})
    assert validate_auth_response("get", get_response(request, authenticator_data=extended), request)


def create_fixture():
    from cryptography.hazmat.primitives.asymmetric import ec

    source = options()
    source["authenticatorSelection"] = {"userVerification": "required"}
    request = normalize_auth_request("create", source, 9)
    numbers = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    cose = {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32), -3: numbers.y.to_bytes(32)}
    data = hashlib.sha256(request["rpId"].encode()).digest() + b"\x45" + bytes(4 + 16)
    data += b"\x00\x03\x01\x02\x03" + cbor(cose)
    response = {"id": "AQID", "rawId": "AQID", "type": "public-key", "clientExtensionResults": {},
        "response": {"clientDataJSON": request["clientData"], "authenticatorData": encoded(data),
        "attestationObject": encoded(cbor({"fmt": "none", "authData": data, "attStmt": {}})),
        "transports": ["internal"]}}
    return request, response, data


@pytest.mark.parametrize("change", [
    lambda data: bytes(32) + data[32:],
    lambda data: data[:32] + b"\x44" + data[33:],
    lambda data: data[:32] + b"\x41" + data[33:],
    lambda data: data[:32] + b"\x05" + data[33:],
    lambda data: data + b"unexpected tail",
])
def test_create_validates_rp_and_flags_even_in_consistent_attestation_envelope(change):
    request, response, data = create_fixture()
    changed = change(data)
    response["response"].update(authenticatorData=encoded(changed),
        attestationObject=encoded(cbor({"fmt": "none", "authData": changed, "attStmt": {}})))
    with pytest.raises(AuthRequestError):
        validate_auth_response("create", response, request)


@pytest.mark.parametrize("envelope", [
    lambda data: cbor({"fmt": "none", "authData": bytes(len(data)), "attStmt": {}}),
    lambda data: cbor({"fmt": "none", "authData": data}),
    lambda data: cbor({"fmt": "none", "authData": data, "attStmt": []}),
    lambda data: cbor({"fmt": "none", "authData": data, "attStmt": {}}) + b"tail",
    lambda data: cbor({"fmt": "none", "authData": data, "attStmt": {"x": [[0] * 64] * 64}}),
    lambda data: b"\xa4" + cbor("fmt") + cbor("none") + cbor("authData") + cbor(data)
        + cbor("authData") + cbor(data) + cbor("attStmt") + cbor({}),
])
def test_attestation_object_must_be_bounded_complete_and_match_authenticator_data(envelope):
    request, response, data = create_fixture()
    response["response"]["attestationObject"] = encoded(envelope(data))
    with pytest.raises(AuthRequestError):
        validate_auth_response("create", response, request)


def test_create_rejects_excluded_credential_and_inconsistent_resident_property():
    request, response, _ = create_fixture()
    request["credentials"] = [{"id": "AQID", "transports": 0}]
    with pytest.raises(AuthRequestError):
        validate_auth_response("create", response, request)
    request["credentials"] = []
    request.update(credProps=True, requireResidentKey=True)
    response["clientExtensionResults"] = {"credProps": {"rk": False}}
    with pytest.raises(AuthRequestError):
        validate_auth_response("create", response, request)
    response["clientExtensionResults"]["credProps"]["rk"] = True
    assert validate_auth_response("create", response, request)


async def test_prepare_cancellation_cleans_helper_and_propagates(monkeypatch, tmp_path):
    entered = asyncio.Event()

    async def start():
        entered.set()
        await asyncio.Event().wait()

    helper = SimpleNamespace(start=start, close=AsyncMock())
    monkeypatch.setattr(browser_auth, "_powershell", lambda: "/synthetic/powershell.exe")
    monkeypatch.setattr(browser_auth, "_WindowsHelper", lambda executable: helper)
    bridge = BrowserAuthBridge(AsyncMock(), lambda: False)
    task = asyncio.create_task(bridge.prepare(tmp_path))
    await asyncio.wait_for(entered.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 30)
    helper.close.assert_awaited_once()
    assert bridge._helper is None and not bridge.launch_args


@pytest.mark.parametrize("origin", ["https://private-origin@example.com", "https://example.com/path?secret=test", "http://example.com"])
async def test_rejected_request_never_reports_unvalidated_origin(origin):
    bridge, helper, worker, _, reports = await setup_bridge(True)
    try:
        source = options()
        source["extensions"].update(prf={"eval": {"first": "AQIDBA"}})
        source["extensions"]["remoteDesktopClientOverride"]["origin"] = origin
        worker.event("create", data=source)
        await until(lambda: worker.completed)
        assert not helper.requests
        assert reports[-1]["origin"] == ""
        assert origin not in json.dumps(reports)
    finally:
        await bridge.close()
