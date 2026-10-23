"""Synthetic regression checks at asynchronous continuity admission boundaries."""

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.portable_history import MAX_BYTES, portable_history
from kira.service.server import Service
from kira.service.storage import encode


class NoInference:
    async def run(self, _ctx):
        raise AssertionError("This regression must never start a provider")

    async def close(self):
        pass


@pytest_asyncio.fixture
async def continuity_service(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-admission-") as directory:
        base = Path(directory)
        service = Service(Paths(*(base / name for name in ("config", "data", "state", "runtime"))),
                          backend_factory=lambda *_: NoInference())
        await service.start()
        project = (await service.dispatch("project.create", {
            "root": str(tmp_path), "name": "Synthetic admission project"}))["project"]
        session = (await service.dispatch("session.create", {
            "project_id": project["id"], "backend": "openai_chatgpt", "model": "fixture-model"}))["session"]
        service.prompt_queue.set_paused(project["id"], True)
        try:
            yield service, project, session
        finally:
            service._closing = False
            service._settings_changing = False
            await service.close()


@pytest.mark.parametrize("gate", ["_closing", "stopping"])
async def test_queue_rechecks_service_transition_after_account_validation(continuity_service, monkeypatch, gate):
    service, _, session = continuity_service
    entered, release = asyncio.Event(), asyncio.Event()

    async def prepare(settings, **_kwargs):
        entered.set()
        await release.wait()
        return settings

    monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
    pending = asyncio.create_task(service.dispatch("queue.add", {
        "session_id": session["id"], "request_id": "late-queued-request", "prompt": "Synthetic queued task"}))
    await asyncio.wait_for(entered.wait(), 30)
    if gate == "stopping":
        service.stopping.set()
    else:
        setattr(service, gate, True)
    release.set()
    with pytest.raises(KiraError) as error:
        await asyncio.wait_for(pending, 30)
    assert error.value.code in {"BUSY", "SHUTTING_DOWN"}
    assert not service.store.all("SELECT * FROM prompt_queue")
    assert not service.store.all("SELECT * FROM runs")


async def test_oversized_latest_turn_preserves_request_answer_and_recent_tool_evidence(continuity_service):
    service, _, session = continuity_service
    prompt = "REQUEST-BEGIN\n" + "Original user context. " * 5000 + "\nREQUEST-END"
    run, _ = service.store.create_run(session["id"], "large-turn", prompt)
    service.store.event(run["id"], "message.completed", {"content": "Earlier assistant discussion"})
    for index in range(50):
        service.store.event(run["id"], "tool.completed", {
            "name": f"tool-number-{index:02}", "result": {"text": "Recorded evidence. " * 700}})
    service.store.event(run["id"], "message.user", {
        "source": "steer", "content": "LATEST-STEERING: focus on the final conclusion"})
    service.store.event(run["id"], "message.completed", {
        "content": "ANSWER-BEGIN\n" + "Important conclusion. " * 7000 + "\nANSWER-END",
        "provider_messages": [{"role": "assistant", "tool_calls": [{"id": "never-replay"}],
                               "reasoning": "never-transfer"}]})
    service.store.event(run["id"], "tool.completed", {
        "name": "last-observation", "result": {"text": "The last recorded observation"}})
    service.store.event(run["id"], "run.state", {"state": "completed"})

    history, receipt = portable_history(service.store, session["id"], "next-run")
    rendered = encode(history)
    assert history and len(rendered.encode()) <= MAX_BYTES
    assert history[0]["run_id"] == run["id"] and history[0]["role"] == "user"
    assert "REQUEST-BEGIN" in rendered and "REQUEST-END" in rendered
    assert "ANSWER-BEGIN" in rendered and "ANSWER-END" in rendered
    assert "LATEST-STEERING" in rendered and "last-observation" in rendered
    assert "tool-number-00" not in rendered
    assert rendered.index("LATEST-STEERING") < rendered.index("ANSWER-BEGIN") < rendered.index("last-observation")
    assert "never-replay" not in rendered and "never-transfer" not in rendered
    assert "omitted" in rendered and receipt["truncated"] and receipt["runs_included"] == 1
    assert receipt["native_state_transferred"] is False


async def test_oversized_unicode_context_retains_valid_bounded_head_and_tail(continuity_service):
    service, _, session = continuity_service
    prompt = "USER-START\n" + "🌍資料" * 50_000 + "\nUSER-END"
    answer = "ANSWER-START\n" + "🧭結果" * 50_000 + "\nANSWER-END"
    run, _ = service.store.create_run(session["id"], "unicode-turn", prompt)
    service.store.event(run["id"], "message.completed", {"content": answer})
    service.store.event(run["id"], "run.state", {"state": "completed"})
    history, receipt = portable_history(service.store, session["id"], "next-run")
    encoded = encode(history).encode("utf-8")
    assert len(encoded) <= MAX_BYTES and encoded.decode("utf-8") == encode(history)
    assert len(history) == 2 and history[0]["run_id"] == run["id"]
    assert "USER-START" in history[0]["content"] and "USER-END" in history[0]["content"]
    assert "ANSWER-START" in history[1]["content"] and "ANSWER-END" in history[1]["content"]
    assert "�" not in encode(history) and receipt["truncated"]


async def test_large_tool_only_turn_still_retains_user_request(continuity_service):
    service, _, session = continuity_service
    run, _ = service.store.create_run(session["id"], "tool-only-turn", "Inspect the synthetic build logs")
    for index in range(45):
        service.store.event(run["id"], "tool.completed", {
            "name": "read_fixture", "result": {"line": index, "text": "synthetic evidence " * 500}})
    service.store.event(run["id"], "run.state", {"state": "interrupted"})
    history, receipt = portable_history(service.store, session["id"], "next-run")
    assert history and "Inspect the synthetic build logs" in history[0]["content"]
    assert '"line":44' in history[-1]["content"]
    assert receipt["truncated"] and len(encode(history).encode()) <= MAX_BYTES


async def test_empty_tool_call_message_does_not_replace_latest_meaningful_answer(continuity_service):
    service, _, session = continuity_service
    run, _ = service.store.create_run(session["id"], "empty-final-message", "Inspect the synthetic logs")
    service.store.event(run["id"], "message.completed", {"content": "Keep this verified answer excerpt"})
    for index in range(45):
        service.store.event(run["id"], "tool.completed", {
            "name": "read_fixture", "result": {"line": index, "text": "synthetic evidence " * 500}})
    service.store.event(run["id"], "message.completed", {"content": "", "provider_messages": [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "unfinished-native-call"}]}]})
    service.store.event(run["id"], "run.state", {"state": "interrupted"})
    history, receipt = portable_history(service.store, session["id"], "next-run")
    assert "Keep this verified answer excerpt" in encode(history)
    assert "unfinished-native-call" not in encode(history) and receipt["truncated"]
