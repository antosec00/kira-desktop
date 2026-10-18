"""Private, human-only WebAuthn bridge from an owned Chromium actor to Windows.

Credential request/response bodies travel only over owned process pipes and the
extension's CDP worker session. They must never become tool results or events.
"""

import asyncio
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

from kira.domain.contracts import Json

MAX_AUTH_BYTES = 262144
MAX_MANUAL_WAIT = 120
_SOURCE = Path(__file__).resolve().parent
_ERRORS = {"NotAllowedError", "NotSupportedError", "SecurityError", "AbortError",
           "InvalidStateError", "ConstraintError", "UnknownError", "OperationError"}
_TRANSPORTS = {"usb": 1, "nfc": 2, "ble": 4, "internal": 16, "hybrid": 32}
_ALGORITHMS = {-7, -35, -36, -257, -258, -259, -37, -38, -39, -8}


class AuthRequestError(ValueError):
    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name


def _reject(message: str, name: str = "NotSupportedError") -> NoReturn:
    raise AuthRequestError(name, message)


def _keys(value: Any, allowed: set[str]) -> Json:
    if not isinstance(value, dict) or set(value) - allowed:
        _reject("Unsupported authentication options")
    return value


def _string(value: Any, maximum: int = 1024, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > maximum or "\x00" in value
            or (not value and not empty)):
        _reject("Invalid authentication value", "SecurityError")
    return value


def _binary(value: Any, maximum: int = 1024) -> str:
    text = _string(value, maximum * 2)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        _reject("Invalid authentication encoding", "SecurityError")
    try:
        decoded = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        _reject("Invalid authentication encoding", "SecurityError")
    if not 1 <= len(decoded) <= maximum:
        _reject("Authentication value exceeds its limit", "SecurityError")
    if base64.urlsafe_b64encode(decoded).decode().rstrip("=") != text:
        _reject("Noncanonical authentication encoding", "SecurityError")
    return text


def _choice(value: Any, choices: dict[str, int]) -> int:
    if not isinstance(value, str) or value not in choices:
        _reject("Unsupported authentication preference")
    return choices[value]


def _credentials(value: Any, api_version: int) -> list[Json]:
    if not isinstance(value, list) or len(value) > 64:
        _reject("Unsupported credential list")
    result = []
    for entry in value:
        entry = _keys(entry, {"type", "id", "transports"})
        if entry.get("type") != "public-key":
            _reject("Unsupported credential type")
        transports = entry.get("transports", [])
        if not isinstance(transports, list) or len(transports) > len(_TRANSPORTS):
            _reject("Unsupported authenticator transports")
        mask = 0
        for transport in transports:
            if transport == "hybrid" and api_version < 6:
                _reject("This Windows version does not support hybrid authentication")
            mask |= _choice(transport, _TRANSPORTS)
        result.append({"id": _binary(entry.get("id")), "transports": mask})
    return result


def _origin(value: Any) -> tuple[str, str]:
    origin = _string(value, 2048)
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        _reject("Invalid authentication origin", "SecurityError")
    if (parsed.scheme != "https" or parsed.username or parsed.password or not host
            or parsed.path or parsed.query or parsed.fragment or host.endswith(".")
            or not re.fullmatch(r"[a-z0-9.-]+", host) or (port is not None and port < 1)):
        _reject("Authentication requires a valid HTTPS origin", "SecurityError")
    if origin != "https://" + host + (f":{port}" if port is not None else ""):
        _reject("Noncanonical authentication origin", "SecurityError")
    return origin, host


def _metadata_origin(options: Any) -> str:
    """Retain only a validated browser-supplied origin for rejected requests."""
    if not isinstance(options, dict) or not isinstance(options.get("extensions"), dict):
        return ""
    override = options["extensions"].get("remoteDesktopClientOverride")
    if not isinstance(override, dict):
        return ""
    try:
        return _origin(override.get("origin"))[0]
    except AuthRequestError:
        return ""


def normalize_auth_request(operation: str, options: Any, api_version: int) -> Json:
    """Validate browser-injected origin and preserve the supported Win32 contract.

    The caller must obtain options from the owned extension worker, never from a
    page binding, DOM, tool arguments or the currently selected URL.
    """
    if operation not in {"create", "get"} or api_version < 3:
        _reject("Windows WebAuthn version is not supported")
    common = {"challenge", "timeout", "extensions", "hints"}
    fields = ({"rp", "user", "pubKeyCredParams", "authenticatorSelection",
               "attestation", "excludeCredentials", "attestationFormats"} if operation == "create"
              else {"rpId", "userVerification", "allowCredentials"})
    options = _keys(options, common | fields)
    if options.get("hints", []) != [] or options.get("attestationFormats", []) != []:
        _reject("Authentication hints or attestation formats are not supported")
    extensions = _keys(options.get("extensions"),
                       {"remoteDesktopClientOverride", "credProps"} if operation == "create"
                       else {"remoteDesktopClientOverride"})
    override = _keys(extensions.get("remoteDesktopClientOverride"),
                     {"origin", "sameOriginWithAncestors"})
    if override.get("sameOriginWithAncestors") is not True:
        _reject("Cross-origin frame authentication is not supported")
    origin, host = _origin(override.get("origin"))
    rp = (_keys(options.get("rp"), {"id", "name"}).get("id") if operation == "create"
          else options.get("rpId"))
    rp = _string(rp, 253)
    if (not re.fullmatch(r"[a-z0-9.-]+", rp) or rp.startswith(".") or rp.endswith(".")
            or not (host == rp or host.endswith("." + rp))):
        _reject("Authentication origin does not match its relying party", "SecurityError")
    timeout = options.get("timeout", 180000)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300000:
        _reject("Unsupported authentication timeout")
    challenge = _binary(options.get("challenge"), 4096)
    client_data = json.dumps({"type": "webauthn." + operation, "challenge": challenge,
                              "origin": origin, "crossOrigin": False}, separators=(",", ":"))
    result: Json = {"operation": operation, "origin": origin, "rpId": rp,
                    "timeout": timeout, "clientData": base64.urlsafe_b64encode(
                        client_data.encode()).decode().rstrip("=")}
    verification = {"required": 1, "preferred": 2, "discouraged": 3}
    if operation == "get":
        result.update(userVerification=_choice(options.get("userVerification", "preferred"),
                                                verification),
                      credentials=_credentials(options.get("allowCredentials", []), api_version))
        return result
    user = _keys(options.get("user"), {"id", "name", "displayName"})
    selection = _keys(options.get("authenticatorSelection", {}),
                      {"authenticatorAttachment", "residentKey", "requireResidentKey",
                       "userVerification"})
    required = selection.get("requireResidentKey", False)
    if not isinstance(required, bool):
        _reject("Invalid resident credential preference")
    resident = selection.get("residentKey", "required" if required else "discouraged")
    _choice(resident, {"required": 1, "preferred": 2, "discouraged": 3})
    params = options.get("pubKeyCredParams")
    if not isinstance(params, list) or not 1 <= len(params) <= 32:
        _reject("Unsupported credential algorithms")
    algorithms = []
    for param in params:
        param = _keys(param, {"type", "alg"})
        algorithm = param.get("alg")
        if (param.get("type") != "public-key" or isinstance(algorithm, bool)
                or not isinstance(algorithm, int) or algorithm not in _ALGORITHMS):
            _reject("Unsupported credential algorithm")
        algorithms.append(algorithm)
    cred_props = extensions.get("credProps", False)
    if not isinstance(cred_props, bool):
        _reject("Invalid credential properties extension")
    result.update(
        rpName=_string(options["rp"].get("name")),
        userId=_binary(user.get("id"), 64), userName=_string(user.get("name"), empty=True),
        userDisplayName=_string(user.get("displayName"), empty=True), algorithms=algorithms,
        attachment=_choice(selection.get("authenticatorAttachment", "any"),
                           {"any": 0, "platform": 1, "cross-platform": 2}),
        requireResidentKey=resident == "required", preferResidentKey=resident == "preferred",
        userVerification=_choice(selection.get("userVerification", "preferred"), verification),
        attestation=_choice(options.get("attestation", "none"),
                            {"none": 1, "indirect": 2, "direct": 3}),
        credProps=cred_props,
        credentials=_credentials(options.get("excludeCredentials", []), api_version),
    )
    return result


def _decode_binary(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _attestation_item(data: bytes, position: int, depth: int = 0,
                      budget: list[int] | None = None) -> tuple[Any, int]:
    """Bounded definite CBOR sufficient for attestation objects/extensions.

    Attestation signatures and trust chains remain the relying party's job.
    This parser binds the envelope's authData to the separately returned bytes.
    """
    if budget is None:
        budget = [512]
    budget[0] -= 1
    if depth > 8 or budget[0] < 0 or position >= len(data):
        raise ValueError
    initial, position = data[position], position + 1
    major, length = initial >> 5, initial & 31
    if major == 7:
        if length not in {20, 21, 22}:
            raise ValueError
        return {20: False, 21: True, 22: None}[length], position
    if length >= 24:
        size = {24: 1, 25: 2, 26: 4, 27: 8}.get(length)
        if size is None or position + size > len(data):
            raise ValueError
        length = int.from_bytes(data[position:position + size])
        position += size
    if major == 0:
        return length, position
    if major == 1:
        return -1 - length, position
    if major in {2, 3}:
        if length > (131072 if major == 2 else 4096) or position + length > len(data):
            raise ValueError
        value = data[position:position + length]
        return value if major == 2 else value.decode("utf-8"), position + length
    if major in {4, 5}:
        if length > 64:
            raise ValueError
        values = []
        for _ in range(length * (2 if major == 5 else 1)):
            item, position = _attestation_item(data, position, depth + 1, budget)
            values.append(item)
        if major == 4:
            return values, position
        result = {}
        for index in range(0, len(values), 2):
            key = values[index]
            if type(key) not in {int, str} or key in result:
                raise ValueError
            result[key] = values[index + 1]
        return result, position
    raise ValueError


def _authenticator_data(value: str, request: Json, *, attested: bool) -> bytes:
    data = _decode_binary(value)
    if len(data) < 37 or data[:32] != hashlib.sha256(request["rpId"].encode("utf-8")).digest():
        raise ValueError
    flags = data[32]
    if (not flags & 1 or request["userVerification"] == 1 and not flags & 4
            or bool(flags & 0x40) != attested or flags & 0x10 and not flags & 8):
        raise ValueError
    return data


def _authenticator_tail(data: bytes, position: int) -> None:
    if data[32] & 0x80:
        extensions, position = _attestation_item(data, position)
        if not isinstance(extensions, dict) or any(not isinstance(key, str) for key in extensions):
            raise ValueError
    if position != len(data):
        raise ValueError


def _cose_item(data: bytes, position: int, depth: int = 0) -> tuple[Any, int]:
    """Read only the bounded definite CBOR forms used by public COSE keys."""
    if depth > 3 or position >= len(data):
        raise ValueError
    initial = data[position]
    position += 1
    major, length = initial >> 5, initial & 31
    if length >= 24:
        sizes = {24: 1, 25: 2, 26: 4, 27: 8}
        size = sizes.get(length)
        if size is None or position + size > len(data):
            raise ValueError
        length = int.from_bytes(data[position:position + size])
        position += size
    if major == 0:
        return length, position
    if major == 1:
        return -1 - length, position
    if major == 2:
        if length > 2048 or position + length > len(data):
            raise ValueError
        return data[position:position + length], position + length
    if major in {4, 5}:
        if length > 16:
            raise ValueError
        values: list[Any] = []
        for _ in range(length * (2 if major == 5 else 1)):
            item, position = _cose_item(data, position, depth + 1)
            values.append(item)
        if major == 4:
            return values, position
        result = {}
        for index in range(0, len(values), 2):
            key = values[index]
            if not isinstance(key, int) or key in result:
                raise ValueError
            result[key] = values[index + 1]
        return result, position
    raise ValueError


def _public_key_fields(authenticator_data: str, credential_id: str,
                       algorithms: list[int]) -> tuple[int, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

    data = _decode_binary(authenticator_data)
    if len(data) < 55 or not data[32] & 0x40:
        raise ValueError
    length = struct.unpack_from(">H", data, 53)[0]
    if not 1 <= length <= 1024 or data[55:55 + length] != _decode_binary(credential_id):
        raise ValueError
    key, end = _cose_item(data, 55 + length)
    _authenticator_tail(data, end)
    if not isinstance(key, dict) or key.get(3) not in algorithms:
        raise ValueError
    algorithm = key[3]
    public: Any
    if key.get(1) == 2:
        curves: dict[int, tuple[int, Any, int]] = {
            1: (-7, ec.SECP256R1, 32), 2: (-35, ec.SECP384R1, 48),
            3: (-36, ec.SECP521R1, 66)}
        curve_id = key.get(-1)
        if not isinstance(curve_id, int):
            raise ValueError
        curve = curves.get(curve_id)
        if curve is None or curve[0] != algorithm:
            raise ValueError
        x, y = key.get(-2), key.get(-3)
        if not isinstance(x, bytes) or not isinstance(y, bytes) or len(x) != curve[2] or len(y) != curve[2]:
            raise ValueError
        public = ec.EllipticCurvePublicNumbers(int.from_bytes(x), int.from_bytes(y), curve[1]()).public_key()
    elif key.get(1) == 3 and algorithm in {-257, -258, -259, -37, -38, -39}:
        modulus, exponent = key.get(-1), key.get(-2)
        if (not isinstance(modulus, bytes) or not 256 <= len(modulus) <= 1024
                or not isinstance(exponent, bytes) or not 1 <= len(exponent) <= 8):
            raise ValueError
        public = rsa.RSAPublicNumbers(int.from_bytes(exponent), int.from_bytes(modulus)).public_key()
    elif key.get(1) == 1 and key.get(-1) == 6 and algorithm == -8:
        x = key.get(-2)
        if not isinstance(x, bytes) or len(x) != 32:
            raise ValueError
        public = ed25519.Ed25519PublicKey.from_public_bytes(x)
    else:
        raise ValueError
    der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return algorithm, base64.urlsafe_b64encode(der).decode().rstrip("=")


def validate_auth_response(operation: str, response: Any, request: Json) -> str:
    """Check native output identity and client data before returning it to Chromium."""
    try:
        if operation not in {"create", "get"} or request.get("operation") != operation:
            raise ValueError
        value = _keys(response, {"id", "rawId", "type", "response",
                                 "clientExtensionResults", "authenticatorAttachment"})
        raw_id = _binary(value.get("rawId"))
        if value.get("id") != raw_id or value.get("type") != "public-key":
            raise ValueError
        credential_ids = {credential["id"] for credential in request["credentials"]}
        if (operation == "get" and credential_ids and raw_id not in credential_ids
                or operation == "create" and raw_id in credential_ids):
            raise ValueError
        attachment = value.get("authenticatorAttachment")
        if attachment is not None and attachment not in {"platform", "cross-platform"}:
            raise ValueError
        fields = ({"clientDataJSON", "attestationObject", "transports", "authenticatorData",
                   "publicKey", "publicKeyAlgorithm"} if operation == "create"
                  else {"clientDataJSON", "authenticatorData", "signature", "userHandle"})
        payload = _keys(value.get("response"), fields)
        if payload.get("clientDataJSON") != request["clientData"]:
            raise ValueError
        extensions = _keys(value.get("clientExtensionResults", {}),
                           {"credProps"} if operation == "create" else set())
        if operation == "create":
            attestation = _decode_binary(_binary(payload.get("attestationObject"), 131072))
            auth_data = _binary(payload.get("authenticatorData"), 131072)
            data = _authenticator_data(auth_data, request, attested=True)
            envelope, end = _attestation_item(attestation, 0)
            if (end != len(attestation) or not isinstance(envelope, dict)
                    or set(envelope) != {"fmt", "authData", "attStmt"}
                    or not isinstance(envelope["fmt"], str) or not 1 <= len(envelope["fmt"]) <= 64
                    or not isinstance(envelope["attStmt"], dict) or envelope["authData"] != data):
                raise ValueError
            algorithm, public_key = _public_key_fields(auth_data, raw_id, request["algorithms"])
            if (payload.get("publicKeyAlgorithm", algorithm) != algorithm
                    or payload.get("publicKey", public_key) != public_key):
                raise ValueError
            # These are derived from the authenticator's actual COSE key, never
            # from the first algorithm offered by the relying party.
            payload = {**payload, "publicKeyAlgorithm": algorithm, "publicKey": public_key}
            value = {**value, "response": payload}
            transports = payload.get("transports", [])
            if (not isinstance(transports, list) or len(transports) > len(_TRANSPORTS)
                    or any(not isinstance(item, str) or item not in _TRANSPORTS
                           for item in transports)):
                raise ValueError
            if request.get("credProps"):
                props = _keys(extensions.get("credProps"), {"rk"})
                if (not isinstance(props.get("rk"), bool)
                        or request["requireResidentKey"] and not props["rk"]):
                    raise ValueError
            elif extensions:
                raise ValueError
        else:
            auth_data = _binary(payload.get("authenticatorData"), 131072)
            _authenticator_tail(_authenticator_data(auth_data, request, attested=False), 37)
            _binary(payload.get("signature"), 131072)
            if payload.get("userHandle") is not None:
                _binary(payload["userHandle"], 64)
        encoded = json.dumps(value, separators=(",", ":"))
        if len(encoded.encode()) > MAX_AUTH_BYTES:
            raise ValueError
        return encoded
    except (ValueError, TypeError, KeyError):
        raise AuthRequestError("OperationError", "Native authentication result could not be verified") from None


def _powershell() -> str | None:
    if sys.platform != "linux" or not (os.environ.get("WSL_INTEROP")
            or os.environ.get("WSL_DISTRO_NAME") or "microsoft" in platform.release().lower()):
        return None
    discovered = shutil.which("powershell.exe")
    candidate = Path(discovered or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    return str(candidate) if candidate.is_absolute() and candidate.is_file() else None


class _WindowsHelper:
    def __init__(self, executable: str) -> None:
        self.executable = executable
        self.process: asyncio.subprocess.Process | None = None
        self._nonce = ""

    async def start(self) -> Json:
        script = (_SOURCE / "windows_webauthn.ps1").read_text()
        encoded = base64.b64encode(script.encode("utf-16-le")).decode()
        self.process = await asyncio.create_subprocess_exec(
            self.executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_AUTH_BYTES + 1)
        assert self.process.stdin is not None
        source = base64.b64encode((_SOURCE / "windows_webauthn.cs").read_bytes())
        self.process.stdin.write(source + b"\n")
        await self.process.stdin.drain()
        return await asyncio.wait_for(self._read(), 20)

    async def _read(self) -> Json:
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("Authentication helper is unavailable")
        line = await self.process.stdout.readline()
        if not line or len(line) > MAX_AUTH_BYTES:
            raise RuntimeError("Authentication helper disconnected")
        result = json.loads(line)
        if not isinstance(result, dict):
            raise RuntimeError("Invalid authentication helper response")
        return result

    async def _send(self, value: Json) -> None:
        if self.process is None or self.process.stdin is None or self.process.returncode is not None:
            raise RuntimeError("Authentication helper disconnected")
        self.process.stdin.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
        await self.process.stdin.drain()

    async def perform(self, request: Json) -> Json:
        self._nonce = uuid.uuid4().hex
        await self._send({"command": "perform", "nonce": self._nonce, "request": request})
        result = await asyncio.wait_for(self._read(), request["timeout"] / 1000 + 10)
        if result.get("nonce") != self._nonce or result.get("type") != "result":
            raise RuntimeError("Authentication response identity changed")
        self._nonce = ""
        return result

    async def cancel(self) -> None:
        if self._nonce:
            await self._send({"command": "cancel", "nonce": self._nonce})

    async def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.returncode is None:
                await self._send({"command": "close"})
                if process.stdin is not None:
                    process.stdin.close()
                await asyncio.wait_for(process.wait(), 5)
        except (OSError, RuntimeError, asyncio.TimeoutError):
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        finally:
            self.process = None
            self._nonce = ""


class BrowserAuthBridge:
    def __init__(self, on_state: Callable[[Json], Awaitable[None]],
                 manual_ready: Callable[[], bool]) -> None:
        self.on_state = on_state
        self.manual_ready = manual_ready
        self.launch_args: list[str] = []
        self.ignore_default_args: list[str] = []
        self.capabilities: Json = {"windows_webauthn": False, "platform_authenticator": False}
        self._helper: _WindowsHelper | None = None
        self._worker: Any = None
        self._loop: asyncio.Task[Any] | None = None
        self._pending: asyncio.Task[Any] | None = None
        self._pending_id: int | None = None
        self._manual = asyncio.Event()
        self._cancel = asyncio.Event()
        self._closed = False
        self._api_version = 0
        self._extension_origin = ""

    async def prepare(self, profile: Path) -> bool:
        if not profile.is_absolute() or profile.is_symlink() or self._helper is not None:
            return False
        executable = _powershell()
        if executable is None:
            return False
        helper = _WindowsHelper(executable)
        try:
            available = await helper.start()
            version = available.get("apiVersion")
            if (available.get("type") != "availability" or available.get("ok") is not True
                    or isinstance(version, bool) or not isinstance(version, int) or version < 3):
                await helper.close()
                return False
            extension = _SOURCE / "auth_extension"
            manifest = json.loads((extension / "manifest.json").read_text())
            digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
            self._extension_origin = "chrome-extension://" + "".join(
                chr(ord("a") + int(c, 16)) for c in digest)
            self._helper = helper
            self._api_version = version
            self.launch_args = [f"--disable-extensions-except={extension}",
                                f"--load-extension={extension}"]
            self.ignore_default_args = ["--disable-extensions"]
            self.capabilities = {"windows_webauthn": True,
                                 "platform_authenticator": available.get(
                                     "platformAuthenticatorAvailable") is True}
            return True
        except asyncio.CancelledError:
            await helper.close()
            raise
        except Exception:
            await helper.close()
            return False

    async def attach(self, context: Any) -> bool:
        if self._helper is None or self._closed:
            return False
        expected = self._extension_origin + "/worker.js"
        try:
            workers = [worker for worker in context.service_workers if worker.url == expected]
            self._worker = workers[0] if workers else await context.wait_for_event(
                "serviceworker", predicate=lambda worker: worker.url == expected, timeout=10000)
            await asyncio.wait_for(self._worker.evaluate("kiraAuthentication.attach()"), 5)
            self._loop = asyncio.create_task(self._events())
            return True
        except Exception:
            await self.close()
            return False

    async def set_manual(self, manual: bool) -> None:
        if manual:
            if self.manual_ready():
                self._manual.set()
            return
        else:
            self._manual.clear()
            if self._pending is not None:
                self._cancel.set()
                if self._helper is not None:
                    await self._helper.cancel()

    async def _state(self, operation: str, origin: str, result: str, pending: bool) -> None:
        await self.on_state({"operation": operation, "origin": origin,
                             "result": result, "pending": pending})

    async def _complete(self, kind: str, request_id: int, value: Json) -> None:
        if self._worker is not None:
            await asyncio.wait_for(self._worker.evaluate(
                "value => kiraAuthentication.complete(value)",
                {"kind": kind, "requestId": request_id, **value}), 5)

    async def _error(self, kind: str, request_id: int, name: str, message: str) -> None:
        await self._complete(kind, request_id, {"error": {"name": name, "message": message}})

    async def _events(self) -> None:
        try:
            while not self._closed:
                event = await self._worker.evaluate("kiraAuthentication.next()")
                if not isinstance(event, dict) or event.get("kind") not in {
                        "create", "get", "uvpaa", "cancel"}:
                    raise RuntimeError("Invalid authentication event")
                kind, detail = event["kind"], event.get("detail", {})
                request_id = detail.get("requestId")
                if (isinstance(request_id, bool) or not isinstance(request_id, int)
                        or not -(2**31) <= request_id < 2**31):
                    raise RuntimeError("Invalid authentication identity")
                if kind == "uvpaa":
                    await self._complete(kind, request_id,
                                         {"isUvpaa": self.capabilities["platform_authenticator"]})
                elif kind == "cancel":
                    if request_id == self._pending_id:
                        self._cancel.set()
                        if self._helper is not None:
                            await self._helper.cancel()
                elif self._pending is not None and not self._pending.done():
                    await self._error(kind, request_id, "NotAllowedError",
                                      "Another authentication request is pending")
                else:
                    self._cancel = asyncio.Event()
                    self._pending_id = request_id
                    self._pending = asyncio.create_task(self._request(kind, request_id, detail))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._cancel.set()
            try:
                if self._worker is not None:
                    await asyncio.wait_for(self._worker.evaluate("kiraAuthentication.detach()"), 2)
                if self._helper is not None:
                    await self._helper.cancel()
            except Exception:
                pass
            self.capabilities = {"windows_webauthn": False, "platform_authenticator": False}
            await self._state("", "", "unavailable", False)

    async def _request(self, kind: str, request_id: int, detail: Json) -> None:
        origin = ""
        native_started = False
        cancelled: asyncio.Task[Any] = asyncio.create_task(self._cancel.wait())
        native: asyncio.Task[Any] | None = None
        try:
            serialized = detail.get("requestDetailsJson")
            if not isinstance(serialized, str) or len(serialized.encode()) > MAX_AUTH_BYTES:
                _reject("Authentication request exceeds its limit", "SecurityError")
            options = json.loads(serialized)
            origin = _metadata_origin(options)
            request = normalize_auth_request(kind, options, self._api_version)
            origin = request["origin"]
            if not self._manual.is_set() or not self.manual_ready():
                await self._state(kind, origin, "needs_manual", True)
            async with asyncio.timeout(MAX_MANUAL_WAIT):
                while not (self._manual.is_set() and self.manual_ready()):
                    ready = asyncio.create_task(self._manual.wait())
                    done, _ = await asyncio.wait({ready, cancelled},
                                                 return_when=asyncio.FIRST_COMPLETED)
                    ready.cancel()
                    await asyncio.gather(ready, return_exceptions=True)
                    if cancelled in done:
                        raise AuthRequestError("AbortError", "Authentication was cancelled")
                    if not self.manual_ready():
                        self._manual.clear()
            if self._cancel.is_set() or self._closed:
                raise AuthRequestError("AbortError", "Authentication was cancelled")
            await self._state(kind, origin, "native_prompt", True)
            assert self._helper is not None
            native_started = True
            native = asyncio.create_task(self._helper.perform(request))
            done, _ = await asyncio.wait({native, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done:
                await self._helper.cancel()
                try:
                    await asyncio.wait_for(native, 3)
                except (Exception, asyncio.CancelledError):
                    await self._helper.close()
                raise AuthRequestError("AbortError", "Authentication was cancelled")
            result = await native
            if result.get("ok") is not True:
                error = result.get("error", {})
                name = error.get("name") if isinstance(error, dict) else "UnknownError"
                if name not in _ERRORS:
                    name = "UnknownError"
                raise AuthRequestError(name, "Windows could not complete authentication")
            response_json = validate_auth_response(kind, result.get("response"), request)
            await self._complete(kind, request_id, {"responseJson": response_json})
            await self._state(kind, origin, "completed", False)
        except AuthRequestError as exc:
            try:
                await self._error(kind, request_id, exc.name, str(exc))
            except Exception:
                pass
            state = ("unconfirmed" if native_started and (
                        self._cancel.is_set() or exc.name == "OperationError") else
                     "unsupported" if exc.name == "NotSupportedError" else
                     "cancelled" if exc.name in {"AbortError", "NotAllowedError"} else "failed")
            await self._state(kind, origin, state, False)
        except (Exception, asyncio.CancelledError):
            if native_started and self._helper is not None:
                try:
                    await self._helper.cancel()
                except Exception:
                    pass
                await self._helper.close()
                self._helper = None
                self.capabilities = {"windows_webauthn": False, "platform_authenticator": False}
            try:
                await self._error(kind, request_id, "AbortError",
                                  "Authentication stopped without a confirmed result")
            except Exception:
                pass
            await self._state(kind, origin, "unconfirmed" if native_started else "failed", False)
        finally:
            cancelled.cancel()
            if native is not None and not native.done():
                native.cancel()
                await asyncio.gather(native, return_exceptions=True)
            await asyncio.gather(cancelled, return_exceptions=True)
            self._pending_id = None

    async def close(self) -> None:
        self._closed = True
        self._cancel.set()
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._worker.evaluate("kiraAuthentication.detach()"), 2)
            except Exception:
                pass
        if self._loop is not None:
            self._loop.cancel()
            await asyncio.gather(self._loop, return_exceptions=True)
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
            await asyncio.gather(self._pending, return_exceptions=True)
        if self._helper is not None:
            await self._helper.close()
        self._helper = None
        self._worker = None
