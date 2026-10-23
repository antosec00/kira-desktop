"""Synthetic durable-context and compaction checks; no live account calls."""

import asyncio
import copy
import json

import httpx
import pytest

from kira.backends.direct_context import prune_native, validate_memory
from kira.backends.openai_chatgpt import OpenAIChatGPTBackend, _catalog
from kira.backends.openai_responses import ResponsesError
from kira.domain.context_policy import ContextPolicy, estimate_tokens
from kira.domain.contracts import RunContext


class Auth:
    async def access_token(self, account):
        assert account == "fixture-account"
        return "fixture-token"

    async def status(self):
        return {"active_account_id": "fixture-account"}


def message(text):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}]}


def opaque(identity="compact-1"):
    return {"type": "compaction", "id": identity, "encrypted_content": "opaque-private-fixture"}


def complete(output=None, usage=None):
    event = {"type": "response.completed", "response": {"status": "completed",
             "output": output or [message("Done")], "usage": usage}}
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                         content=("data: " + json.dumps(event) + "\n\n").encode())


def native_fold(body):
    return (body.get("context_management") == [{"type": "compaction", "compact_threshold": 1000}]
            and body.get("text") == {"format": {"type": "text"}} and "tools" not in body)


def unsupported_count():
    return httpx.Response(401, json={"error": {"code": "hardened_oauth_rule_missing",
                                               "type": "rejected_by_hardened_oauth_boundary"}})


def context(tmp_path, *, groups=None, saved=None):
    events, saves, executed, pages = [], [], [], []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(name, arguments):
        executed.append((name, arguments))
        return {"ok": True, "result": "effect-confirmed"}

    async def approve(value):
        raise AssertionError("No permission request expected")

    async def save(value):
        snapshot = copy.deepcopy(value)
        snapshot["revision"] = (saves[-1]["revision"] if saves else (saved or {}).get("revision", 0)) + 1
        saves.append(snapshot)
        return snapshot

    async def page(params):
        pages.append(params)
        offset = int(params.get("after") or 0)
        chunk = (groups or [])[offset:offset + 7]
        next_cursor = str(offset + len(chunk))
        return {"groups": chunk, "next_cursor": next_cursor,
                "has_more": offset + len(chunk) < len(groups or [])}

    ctx = RunContext("run-current", {"root": str(tmp_path), "permission": "trusted_host"},
        {"id": "session", "model": "fixture-model", "provider_generation": 0,
         "settings": {"reasoning_effort": "high", "openai_account_id": "fixture-account"}},
        "Current request is exact", "Current project instructions", emit, execute, approve,
        asyncio.Event(), context_state=saved, context_save=save, context_page=page,
        context_catalog={"context_window": 12_000, "max_context_window": 40_000})
    return ctx, events, saves, executed, pages


def history_groups(count=45):
    return [{"run_id": f"run-{index}", "source_ids": [f"h:{index + 1}"], "cursor": str(index + 1),
             "items": [{"role": "user", "content": f"[h:{index + 1}] Exact source {index}: key-{index}"},
                       {"role": "assistant", "content": f"Source response {index}"}]}
            for index in range(count)]


def test_maximum_catalog_window_is_distinct_and_unknown_is_not_fabricated():
    model = _catalog({"models": [{"visibility": "list", "slug": "fixture", "display_name": "Fixture",
                                "context_window": 272_000, "max_context_window": 872_000}]})[0]
    policy = ContextPolicy.from_catalog(model)
    assert policy.context_window == 872_000 and policy.default_context_window == 272_000
    assert policy.reserved_tokens > 0 and policy.compact_threshold < policy.context_window
    assert policy.source == "provider_catalog_maximum"
    assert ContextPolicy.from_catalog({}).context_window is None
    assert ContextPolicy.from_catalog({"context_window": True}).context_window is None


async def test_more_than_30_turns_all_pages_and_native_state_survive_restart(tmp_path):
    ctx, events, saves, _, pages = context(tmp_path, groups=history_groups())
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        bodies.append(body)
        assert body["model"] == "fixture-model" and body["reasoning"] == {"effort": "high", "summary": "auto"}
        assert body["context_management"][0]["compact_threshold"] > 12_000
        assert "key-0" in json.dumps(body) and "key-44" in json.dumps(body)
        return complete([{"type": "reasoning", "encrypted_content": "private-reasoning"}, message("Done")])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert len(pages) == 7 and len(bodies) == 1
        assert saves[-1]["boundary"] == "response_complete" and len(saves[-1]["items"]) == 93
        assert "private-reasoning" not in json.dumps(events)
        ctx2, _, saves2, _, _ = context(tmp_path, groups=history_groups(), saved=saves[-1])
        ctx2.run_id = "run-next"
        ctx2.prompt = "Continue exactly"
        assert (await backend.run(ctx2))["state"] == "completed"
        assert len(bodies) == 2 and "private-reasoning" in json.dumps(bodies[-1])
        assert sum(item.get("content") == "Current request is exact" for item in bodies[-1]["input"]) == 1
        assert saves2[-1]["revision"] > saves[-1]["revision"]
    finally:
        await backend.close()


async def test_inline_compaction_prunes_prefix_and_never_exposes_opaque_items(tmp_path):
    ctx, events, saves, _, _ = context(tmp_path, groups=history_groups())

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        return complete([opaque("first"), message("Answer"), opaque("last")])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert saves[-1]["items"] == [opaque("last")]
        assert saves[-1]["compaction"]["kind"] == "native"
        assert saves[-1]["compactions"] == 1
        assert "opaque-private-fixture" not in json.dumps(events)
    finally:
        await backend.close()


async def test_manual_native_compaction_has_readable_source_memory_no_chat_message(tmp_path):
    ctx, events, saves, executed, _ = context(tmp_path, groups=history_groups())
    requests = []
    memory = [{"kind": "constraint", "text": "Preserve key-0", "source_ids": ["h:1"], "status": "claim"}]

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        body = json.loads(request.content)
        assert "tools" not in body and body["reasoning"] == {"effort": "high"}
        if native_fold(body):
            assert "Create the historical handoff" not in json.dumps(body)
            assert "Produce a compact factual handoff" not in body["instructions"]
            return complete([opaque("ordinary-checkpoint"), message("Discard auxiliary continuation"), opaque("too-late")],
                            {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})
        assert "context_management" not in body
        assert body["text"]["format"]["name"] == "kira_context_memory"
        return complete([message(json.dumps({"summary": "Preserve key-0", "memory": memory})),
                         opaque("summary-task-must-not-persist")],
                        {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.compact_context(ctx)
        assert result["state"] == "completed" and result["memory"] == memory
        assert result["compacted"] is True and result["method"] == "native"
        assert saves[-1]["items"] == [opaque("ordinary-checkpoint")] and not executed
        assert all(kind not in {"message.completed", "message.delta"} for kind, _ in events)
        assert all("current_run_id" not in save for save in saves)
        assert requests.count("/v1/responses") == 2
        usage = [payload for kind, payload in events if kind == "usage"]
        assert {item["maintenance_phase"] for item in usage} == {"native_fold", "memory_summary"}
        assert len({item["measurement_id"] for item in usage}) == 2
    finally:
        await backend.close()


async def test_explicit_unsupported_native_admission_uses_same_model_structured_checkpoint(tmp_path):
    ctx, _, saves, executed, _ = context(tmp_path, groups=history_groups())
    attempts = []

    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("input_tokens") or request.url.path.endswith("compact"):
            return unsupported_count()
        attempts.append(body)
        if "context_management" in body:
            return httpx.Response(400, json={"error": {"code": "unsupported_parameter",
                                                       "param": "context_management"}})
        return complete([message(json.dumps({"summary": "Known archive keys remain recoverable.", "memory": []}))])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.compact_context(ctx)
        assert result["state"] == "completed" and not executed and len(attempts) == 2
        assert result["compacted"] is True and result["method"] == "structured"
        assert saves[-1]["compaction"]["kind"] == "structured"
        assert all(body["model"] == "fixture-model" and body["reasoning"] == {"effort": "high"}
                   for body in attempts)
        assert saves[-1]["capabilities"]["server_compaction"] is False
    finally:
        await backend.close()


async def test_main_auth_failure_is_not_retried_or_misclassified_as_compaction(tmp_path):
    ctx, _, _, _, _ = context(tmp_path)
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return unsupported_count()

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed"
        assert requests.count("/v1/responses") == 1
    finally:
        await backend.close()


async def test_tools_checkpoint_only_after_all_results_and_unknown_result_never_replayed(tmp_path):
    ctx, events, saves, executed, _ = context(tmp_path)
    rounds = []
    call = {"type": "function_call", "call_id": "tool-1", "namespace": "files", "name": "read_file",
            "arguments": json.dumps({"path": "fixture"}), "status": "completed"}

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        rounds.append(json.loads(request.content))
        if len(rounds) == 1:
            return complete([call])
        raise httpx.ReadError("synthetic disconnect")

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted" and len(executed) == 1
        assert saves[-1]["boundary"] == "tools_complete"
        assert saves[-1]["items"][-1]["type"] == "function_call_output"
        assert len(rounds) == 2
    finally:
        await backend.close()


async def test_model_and_account_boundaries_rebuild_from_archive_not_opaque(tmp_path):
    old = {"schema": 1, "account_id": "other-account", "model": "old-model", "provider_generation": 0,
           "items": [opaque()], "cursor": "45"}
    ctx, _, _, _, _ = context(tmp_path, groups=history_groups(), saved=old)

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        body = json.loads(request.content)
        assert "opaque-private-fixture" not in json.dumps(body)
        assert "key-0" in json.dumps(body) and "key-44" in json.dumps(body)
        return complete()

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
    finally:
        await backend.close()


def test_pruning_retains_unpaired_tool_boundary_and_memory_rejects_fabricated_source():
    calls = [{"type": "function_call", "call_id": "call"}, opaque(),
             {"type": "function_call_output", "call_id": "call", "output": "{}"}]
    assert prune_native(calls) == calls
    with pytest.raises(ResponsesError, match="ungrounded"):
        validate_memory([{"kind": "decision", "text": "A claim", "status": "claim",
                          "source_ids": ["h:foreign"]}], {"h:owned"})
    assert estimate_tokens({"role": "user", "content": "An estimate"}) > 0


async def test_large_archive_folds_repeatedly_without_dropping_any_group(tmp_path):
    groups = history_groups(70)
    for group in groups:
        group["items"][0]["content"] += " Literal fixture text." * 100
    ctx, events, saves, executed, pages = context(tmp_path, groups=groups)
    ctx.context_catalog = {"context_window": 8_000, "max_context_window": 8_000}
    compacted_sources, main_bodies = set(), []

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        body = json.loads(request.content)
        assert body["model"] == "fixture-model"
        maintenance = native_fold(body) or body.get("text", {}).get("format", {}).get("name") == "kira_context_memory"
        assert body["reasoning"] == {"effort": "high", **({} if maintenance else {"summary": "auto"})}
        if native_fold(body):
            return complete([opaque("ordinary-fold"), message("Discard continuation")])
        if body.get("text", {}).get("format", {}).get("name") == "kira_context_memory":
            text = json.dumps(body)
            for index in range(70):
                if f"[h:{index + 1}] Exact source {index}:" in text:
                    compacted_sources.add(index)
            return complete([opaque(), message(json.dumps({"summary": "Sources remain searchable.", "memory": []})),
                             opaque("maintenance-final")])
        main_bodies.append(body)
        text = json.dumps(body)
        for index in range(70):
            if f"[h:{index + 1}] Exact source {index}:" in text:
                compacted_sources.add(index)
        return complete([message("Done")])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed" and not executed
        assert saves[-1]["compactions"] >= 5
        assert compacted_sources == set(range(70))
        assert len(pages) == 10 and len(main_bodies) == 1
        assert main_bodies[0]["input"][-1]["content"] == "Current request is exact"
        assert any(kind == "context.compaction" for kind, _ in events)
    finally:
        await backend.close()


async def test_failed_compaction_preserves_previous_checkpoint_and_does_not_infer_user_turn(tmp_path):
    groups = history_groups(10)
    ctx, events, saves, _, _ = context(tmp_path, groups=groups)
    previous = None

    def handler(request):
        nonlocal previous
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        previous = copy.deepcopy(saves[-1])
        return complete([message(json.dumps({"summary": "Untrusted handoff", "memory": [
            {"kind": "constraint", "text": "Invented origin", "source_ids": ["h:foreign"], "status": "claim"}]}))])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResponsesError, match="ungrounded"):
            await backend.compact_context(ctx)
        assert saves[-1] == previous and all(save.get("compactions", 0) == 0 for save in saves)
        assert not any(kind.startswith("message.") for kind, _ in events)
    finally:
        await backend.close()


async def test_new_user_request_is_never_summarized_before_first_response(tmp_path):
    groups = history_groups(12)
    ctx, _, _, _, _ = context(tmp_path, groups=groups)
    ctx.context_catalog = {"context_window": 8_000}
    ctx.prompt = "Literal next task exact ID KEEP-98989. " * 250
    main_bodies = []

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        body = json.loads(request.content)
        if native_fold(body):
            assert "KEEP-98989" not in json.dumps(body)
            return complete([opaque(), message("Discard continuation")])
        if body.get("text", {}).get("format", {}).get("name") == "kira_context_memory":
            assert "KEEP-98989" not in json.dumps(body)
            return complete([opaque(), message(json.dumps({"summary": "Historical facts", "memory": []})), opaque("last")])
        main_bodies.append(body)
        return complete()

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert main_bodies[-1]["input"][-1]["content"] == ctx.prompt
    finally:
        await backend.close()


async def test_historical_tool_id_cannot_execute_again_after_restart(tmp_path):
    call = {"type": "function_call", "call_id": "old-effect", "namespace": "files", "name": "read_file",
            "arguments": json.dumps({"path": "fixture"}), "status": "completed"}
    old = {"schema": 1, "account_id": "fixture-account", "model": "fixture-model", "provider_generation": 0,
           "items": [call, {"type": "function_call_output", "call_id": "old-effect", "output": "{}"}],
           "cursor": None}
    ctx, _, saves, executed, _ = context(tmp_path, saved=old)

    def handler(request):
        return unsupported_count() if request.url.path.endswith("input_tokens") else complete([call])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "interrupted" and "historical" in result["error"]
        assert not executed and saves[-1]["boundary"] == "bootstrap"
    finally:
        await backend.close()


async def test_context_count_success_reports_measurement_and_keeps_provider_output_reserve(tmp_path):
    ctx, events, _, _, _ = context(tmp_path)

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 100})
        return complete()

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        status = next(payload for kind, payload in events if kind == "context.status")
        assert status["measurement"] == "provider_preflight" and status["preflight_input_tokens"] == 100
        assert status["estimated_tokens"] is None and "input_tokens" not in status
        assert status["reserved_tokens"] > 0
    finally:
        await backend.close()


@pytest.mark.parametrize("past_count", [0, 3])
async def test_manual_empty_context_is_noop_even_with_historical_compaction_count(tmp_path, past_count):
    old = {"schema": 1, "account_id": "fixture-account", "model": "fixture-model", "provider_generation": 0,
           "items": [], "cursor": None, "compactions": past_count}
    ctx, events, saves, _, _ = context(tmp_path, saved=old)

    def handler(request):
        raise AssertionError("An empty context must not invoke provider inference or counting")

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.compact_context(ctx)
        assert result["state"] == "completed" and result["compacted"] is False
        assert result["method"] is None and result["compactions"] == past_count
        assert all(save["compactions"] == past_count for save in saves)
        assert not any(kind == "context.compaction" for kind, _ in events)
    finally:
        await backend.close()


def test_large_windows_keep_proportional_reserve_and_use_catalog_output_budget():
    default = ContextPolicy.from_catalog({"context_window": 272_000, "max_context_window": 872_000})
    assert default.context_window == 872_000
    assert default.reserved_tokens == 156_960
    assert default.compact_threshold == 715_040
    assert default.reservation_source == "local_planning_margin"
    reported = ContextPolicy.from_catalog({"context_window": 872_000, "max_output_tokens": 200_000})
    assert reported.reserved_tokens == 226_160
    assert reported.max_output_tokens == 200_000
    assert reported.reservation_source == "provider_output_metadata_with_planning_margin"
    assert ContextPolicy.from_catalog({"context_window": 100, "max_output_tokens": True}).reserved_tokens == 18
    assert ContextPolicy.from_catalog({"context_window": 10}).compact_threshold == 8


async def test_unknown_catalog_stops_archive_growth_before_transport_limit(tmp_path, monkeypatch):
    from kira.backends import direct_context, openai_chatgpt
    monkeypatch.setattr(direct_context, "MAX_INPUT_BYTES", 15_000)
    monkeypatch.setattr(openai_chatgpt, "MAX_INPUT_BYTES", 15_000)
    groups = history_groups(120)
    for group in groups:
        group["items"][0]["content"] += "x" * 1000
    ctx, _, saves, executed, pages = context(tmp_path, groups=groups)
    ctx.context_catalog = {}
    requests = []

    def handler(request):
        requests.append(request.url.path)
        assert request.url.path.endswith("input_tokens"), "Unknown capacity must not trigger inference replay"
        assert len(request.content) <= 15_000
        return unsupported_count()

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and "capacity is unavailable" in result["error"]
        assert len(pages) < 18 and not saves and not executed
        assert requests == ["/v1/responses/input_tokens"]
    finally:
        await backend.close()


async def test_known_catalog_compacts_archive_on_transport_pressure(tmp_path, monkeypatch):
    from kira.backends import direct_context, openai_chatgpt
    monkeypatch.setattr(direct_context, "MAX_INPUT_BYTES", 25_000)
    monkeypatch.setattr(openai_chatgpt, "MAX_INPUT_BYTES", 25_000)
    groups = history_groups(60)
    for group in groups:
        group["items"][0]["content"] += "x" * 1000
    ctx, events, saves, _, _ = context(tmp_path, groups=groups)
    ctx.context_catalog = {"context_window": 1_000_000}

    def handler(request):
        assert len(request.content) <= 25_000
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        body = json.loads(request.content)
        if native_fold(body):
            return complete([opaque(), message("Discard continuation")])
        if body.get("text", {}).get("format", {}).get("name") == "kira_context_memory":
            return complete([message(json.dumps({"summary": "Archived facts remain recoverable.", "memory": []})),
                             opaque()])
        return complete()

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed", result
        assert saves[-1]["compactions"] >= 2
        assert all(len(json.dumps(state).encode()) <= 25_000 for state in saves)
        assert any(kind == "context.compaction" and payload.get("reason") == "transport_capacity"
                   for kind, payload in events)
    finally:
        await backend.close()


async def test_oversized_tool_boundary_is_not_saved_or_automatically_reexecuted(tmp_path, monkeypatch):
    from kira.backends import direct_context, openai_chatgpt
    monkeypatch.setattr(direct_context, "MAX_INPUT_BYTES", 15_000)
    monkeypatch.setattr(openai_chatgpt, "MAX_INPUT_BYTES", 15_000)
    ctx, _, saves, executed, _ = context(tmp_path)
    calls = []

    async def large_result(name, arguments):
        executed.append((name, arguments))
        return {"ok": True, "content": "x" * 20_000}

    ctx.execute_tool = large_result

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        calls.append(json.loads(request.content))
        return complete([{"type": "function_call", "call_id": "large-result", "namespace": "files",
            "name": "read_file", "arguments": json.dumps({"path": "fixture"})}])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and "transport limit" in result["error"]
        assert len(executed) == 1 and len(calls) == 1
        assert all(save["boundary"] == "bootstrap" for save in saves)
    finally:
        await backend.close()


async def test_maintenance_usage_has_distinct_stable_measurements_even_if_summary_invalid(tmp_path):
    ctx, events, _, _, _ = context(tmp_path)
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert "measurement_id" not in body
        assert body["model"] == "fixture-model" and body["reasoning"] == {"effort": "high"}
        text = json.dumps({"summary": "Historical handoff", "memory": []}) if len(bodies) == 1 else "invalid JSON"
        return complete([message(text)], {"input_tokens": 50, "output_tokens": 7, "total_tokens": 57})

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        await backend.context_summary([], [], [], ctx, "fixture-account")
        with pytest.raises(ResponsesError, match="Malformed"):
            await backend.context_summary([], [], [], ctx, "fixture-account")
        usage = [payload for kind, payload in events if kind == "usage"]
        assert len(usage) == 2
        assert all(item["scope"] == "context_compaction" and item["round"] == -1 for item in usage)
        assert usage[0]["measurement_id"] != usage[1]["measurement_id"]
        assert all(item["measurement_id"].startswith("context-") for item in usage)
        assert all(item["input_tokens"] == 50 and item["total_tokens"] == 57 for item in usage)
        assert not any(kind.startswith("message.") for kind, _ in events)
    finally:
        await backend.close()


async def test_same_model_smaller_window_rebuilds_archive_with_preserved_revision(tmp_path):
    old = {"schema": 1, "account_id": "fixture-account", "model": "fixture-model", "provider_generation": 0,
           "revision": 12, "items": [{"type": "compaction", "encrypted_content": "old-private-" + "x" * 40_000}],
           "cursor": "45", "compactions": 4, "policy": {"context_window": 100_000},
           "capabilities": {"input_tokens": False}}
    original = copy.deepcopy(old)
    ctx, _, saves, executed, pages = context(tmp_path, groups=history_groups(), saved=old)
    ctx.context_catalog = {"context_window": 8_000, "max_context_window": 8_000}
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert "old-private-" not in json.dumps(body)
        assert "key-0" in json.dumps(body) and "key-44" in json.dumps(body)
        assert body["input"][-1]["content"] == ctx.prompt
        return complete()

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed", result
        assert pages[0]["after"] is None and len(pages) == 7
        assert pages[0]["reset_image_budget"] is True
        assert all("reset_image_budget" not in page for page in pages[1:])
        assert saves[0]["revision"] == 13 and saves[0]["checkpoint_reason"] == "context_window_reduced"
        assert saves[-1]["compactions"] == 4 and len(requests) == 1 and not executed
        assert old == original
    finally:
        await backend.close()


async def test_smaller_window_archive_failure_does_not_replace_prior_checkpoint(tmp_path):
    from kira.domain.contracts import KiraError

    old = {"schema": 1, "account_id": "fixture-account", "model": "fixture-model", "provider_generation": 0,
           "revision": 12, "items": [{"type": "compaction", "encrypted_content": "x" * 40_000}],
           "cursor": "45", "policy": {"context_window": 100_000},
           "capabilities": {"input_tokens": False}}
    original = copy.deepcopy(old)
    ctx, _, saves, executed, _ = context(tmp_path, saved=old)
    ctx.context_catalog = {"context_window": 8_000}

    async def unavailable(params):
        assert params["after"] is None
        raise KiraError("SYNTHETIC_ARCHIVE_UNAVAILABLE", "Synthetic unavailable archive")

    ctx.context_page = unavailable

    def handler(request):
        raise AssertionError("Archive failure must not infer or replay tools")

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "failed" and not saves and not executed
        assert old == original
    finally:
        await backend.close()


def test_encrypted_size_is_not_a_token_estimate_and_does_not_calibrate_ratio():
    from kira.domain.context_policy import calibrated_ratio, has_opaque_context
    short = [{"type": "compaction", "encrypted_content": "x" * 100}]
    large = [{"type": "compaction", "encrypted_content": "x" * 2_000_000}]
    assert has_opaque_context(large)
    assert estimate_tokens(short) == estimate_tokens(large)
    assert calibrated_ratio(large, 100) is None
    assert calibrated_ratio([{"type": "input_image", "image_url": "data:image/png;base64,xxxx"}], 100) is None
    assert calibrated_ratio([{"role": "user", "content": "Visible words"}], 100) is not None


async def test_opaque_metric_is_partial_and_exact_counter_can_replace_it(tmp_path):
    old = {"schema": 1, "account_id": "fixture-account", "model": "fixture-model", "provider_generation": 0,
           "items": [{"type": "compaction", "encrypted_content": "x" * 80_000}],
           "cursor": None, "policy": {"context_window": 10_000}, "bytes_per_token": 4.0}
    ctx, events, _, _, _ = context(tmp_path, saved=old)
    ctx.context_catalog = {"context_window": 10_000}

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        return complete([message("Continue with opaque context")])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed", result
        status = next(payload for kind, payload in events if kind == "context.status")
        assert status["measurement"] == "partial_estimate" and status["opaque_context"] is True
        assert status["estimated_tokens"] is None and status["preflight_input_tokens"] is None
        assert 0 < status["visible_estimated_tokens"] < 5000
    finally:
        await backend.close()

    ctx, events, _, _, _ = context(tmp_path, saved=old)
    ctx.context_catalog = {"context_window": 10_000}

    def counted(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 2000})
        return complete()

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(counted))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        status = next(payload for kind, payload in events if kind == "context.status")
        assert status["measurement"] == "provider_preflight" and status["preflight_input_tokens"] == 2000
        assert status["estimated_tokens"] is None and status["visible_estimated_tokens"] is None
        assert status["opaque_context"] is True
    finally:
        await backend.close()


async def test_many_bootstrap_compactions_with_growing_ciphertext_still_reach_current_prompt(tmp_path):
    groups = history_groups(140)
    for group in groups:
        group["items"][0]["content"] += " Literal fixture detail." * 100
    ctx, events, saves, _, _ = context(tmp_path, groups=groups)
    ctx.context_catalog = {"context_window": 10_000}
    maintenance, main_requests = 0, []
    observed_sources = set()

    def handler(request):
        nonlocal maintenance
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        body = json.loads(request.content)
        text = json.dumps(body)
        for index in range(140):
            if f"[h:{index + 1}] Exact source {index}:" in text:
                observed_sources.add(index)
        if native_fold(body):
            maintenance += 1
            assert maintenance <= 30, "Bootstrap must progress rather than repeatedly folding the same opaque item"
            encrypted = {"type": "compaction", "encrypted_content": "opaque-" + "x" * (19_300 + maintenance * 1500)}
            return complete([encrypted, message("Discard continuation")],
                            {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100})
        if body.get("text", {}).get("format", {}).get("name") == "kira_context_memory":
            return complete([message(json.dumps({"summary": "Archived source details remain recoverable.", "memory": []}))],
                            {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100})
        main_requests.append(body)
        return complete([message("Current task reached")])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.run(ctx)
        assert result["state"] == "completed", result
        assert 8 <= maintenance <= 30 and len(main_requests) == 1
        assert main_requests[0]["input"][-1]["content"] == ctx.prompt
        assert observed_sources == set(range(140))
        assert saves[-1]["measurement"]["estimated_tokens"] is None
        assert saves[-1]["measurement"]["measurement"] == "partial_estimate"
        compacted = [save["compaction"] for save in saves if "compaction" in save]
        assert all(item["after_tokens"] is None for item in compacted)
        assert any(item["before_tokens"] is None and item["before_visible_estimated_tokens"] > 0
                   for item in compacted[1:])
        assert any(payload.get("measurement") == "partial_estimate" for kind, payload in events
                   if kind == "context.status")
    finally:
        await backend.close()


async def test_readable_maintenance_task_never_becomes_durable_native_chat_state(tmp_path):
    ctx, events, saves, executed, _ = context(tmp_path, groups=history_groups())
    current_prompt = 'Return exactly {"answer":"current-task"} for this new request.'
    memory = [{"kind": "constraint", "text": "Preserve key-0", "source_ids": ["h:1"], "status": "claim"}]
    clean = {"type": "compaction", "encrypted_content": "ordinary-native-checkpoint"}
    seen = []

    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return unsupported_count()
        body = json.loads(request.content)
        seen.append(body)
        if native_fold(body):
            assert "Produce a compact factual handoff" not in body["instructions"]
            assert "Create the historical handoff" not in json.dumps(body)
            assert "source_excerpts" not in json.dumps(body)
            assert body["input"][-1]["content"] == "Source response 44"
            return complete([clean, message("AUXILIARY-CONTINUATION-NEVER-PERSIST"),
                             {"type": "compaction", "encrypted_content": "AFTER-AUXILIARY-DO-NOT-REUSE"}])
        if body["text"]["format"].get("name") == "kira_context_memory":
            assert "context_management" not in body
            return complete([message(json.dumps({"summary": "Important previous constraint", "memory": memory})),
                             {"type": "compaction", "encrypted_content": "SUMMARY-TASK-DO-NOT-REUSE"}])
        assert body["text"] == {"format": {"type": "text"}}
        assert body["input"][-1]["content"] == current_prompt
        assert clean in body["input"]
        raw = json.dumps(body)
        assert "AUXILIARY-CONTINUATION" not in raw and "SUMMARY-TASK" not in raw
        assert "Create the historical handoff" not in raw and "source_excerpts" not in raw
        return complete([message('{"answer":"current-task"}')])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.compact_context(ctx))["method"] == "native"
        assert saves[-1]["items"] == [clean]
        next_ctx, next_events, _, _, _ = context(tmp_path, groups=history_groups(), saved=saves[-1])
        next_ctx.run_id = "new-ordinary-turn"
        next_ctx.prompt = current_prompt
        assert (await backend.run(next_ctx))["state"] == "completed"
        assert next(payload["content"] for kind, payload in next_events if kind == "message.completed") == '{"answer":"current-task"}'
        assert len(seen) == 3 and not executed
        assert not any(kind.startswith("message.") for kind, _ in events)
    finally:
        await backend.close()


@pytest.mark.parametrize("preceding", [message("Generated continuation"),
    {"type": "reasoning", "encrypted_content": "new-generated-reasoning"}])
async def test_native_fold_does_not_adopt_compaction_after_auxiliary_generation(tmp_path, preceding):
    ctx, events, _, executed, _ = context(tmp_path)
    ordinary = [{"role": "user", "content": "Previous actual user request"},
                {"role": "assistant", "content": "Previous actual assistant answer"}]

    def handler(request):
        body = json.loads(request.content)
        assert body["input"] == ordinary and "tools" not in body
        return complete([preceding, opaque("late")])

    backend = OpenAIChatGPTBackend(Auth(), transport=httpx.MockTransport(handler))
    try:
        result = await backend.context_fold(ordinary, ctx, "fixture-account", "Stable ordinary Kira instructions")
        assert result == {"supported": True, "items": None}
        assert not executed and not events
    finally:
        await backend.close()
