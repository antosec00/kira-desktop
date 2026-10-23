"""Synthetic admission/lifecycle regressions, with no external provider calls."""

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from kira.domain.contracts import KiraError
from kira.domain.paths import Paths
from kira.service.server import Service


class MaintenanceProvider:
    supports_durable_context = True

    def __init__(self, release, started):
        self.release, self.started = release, started
        self.close_error = False
        self.calls = 0
        self.turns = 0

    async def discover(self, **_kwargs):
        return {"available": True, "models": [{"id": "fixture-model", "context_window": 8192}]}

    async def compact_context(self, _context):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return {"method": "fixture"}

    async def run(self, _context):
        # Service._run records this exception as a failed run, so it cannot fail
        # a test by itself; the fixture teardown checks the recorded counters.
        self.turns += 1
        raise AssertionError("A maintenance operation must not start an ordinary model turn")

    async def close(self):
        if self.close_error:
            raise RuntimeError("Synthetic client cleanup failure")


@pytest_asyncio.fixture
async def context_service(tmp_path):
    with tempfile.TemporaryDirectory(prefix="kira-context-admit-") as directory:
        base = Path(directory)
        release, started = asyncio.Event(), asyncio.Event()
        providers = []

        def backend(*_args):
            provider = MaintenanceProvider(release, started)
            providers.append(provider)
            return provider

        service = Service(Paths(*(base / name for name in ("config", "data", "state", "runtime"))),
                          backend_factory=backend)
        await service.start()
        project = (await service.dispatch("project.create", {"name": "Synthetic context project", "root": str(tmp_path)}))["project"]
        session = (await service.dispatch("session.create", {"project_id": project["id"], "backend": "openai_chatgpt",
            "model": "fixture-model", "settings": {"openai_account_id": "fixture-account"}}))["session"]
        try:
            yield service, project, session, release, started, providers
            # Settle maintenance (and anything it admits afterwards) before
            # checking that no ordinary model turn was started or recorded.
            release.set()
            await asyncio.wait_for(asyncio.gather(*service.contexts.tasks.values(),
                                                  return_exceptions=True), 30)
            assert not any(provider.turns for provider in providers)
            assert not service.store.all("SELECT * FROM runs")
        finally:
            release.set()
            for provider in providers:
                provider.close_error = False
            await service.close()


def params(project, session, request_id="fixture-maintenance"):
    return {"project_id": project["id"], "session_id": session["id"], "request_id": request_id}


async def test_concurrent_retry_replays_one_maintenance_receipt_after_auth_wait(context_service, monkeypatch):
    service, project, session, release, started, providers = context_service
    auth_entered, auth_release = asyncio.Event(), asyncio.Event()
    arrivals = 0

    async def prepare(settings, **_kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            auth_entered.set()
        await auth_release.wait()
        return dict(settings)

    monkeypatch.setattr(service.direct_accounts, "prepare", prepare)
    one = asyncio.create_task(service.dispatch("session.context.compact", params(project, session)))
    two = asyncio.create_task(service.dispatch("session.context.compact", params(project, session)))
    await asyncio.wait_for(auth_entered.wait(), 3)
    auth_release.set()
    results = await asyncio.wait_for(asyncio.gather(one, two, return_exceptions=True), 30)
    await asyncio.wait_for(started.wait(), 3)
    assert all(isinstance(result, dict) for result in results), results
    assert [result["operation"]["request_id"] for result in results] == ["fixture-maintenance"] * 2
    assert service.store.db.execute("SELECT COUNT(*) FROM direct_context_operations").fetchone()[0] == 1
    assert sum(provider.calls for provider in providers) == 1
    release.set()


async def test_client_close_failure_never_strands_project_reservation(context_service):
    service, project, session, release, started, providers = context_service
    await service.dispatch("session.context.compact", params(project, session))
    await asyncio.wait_for(started.wait(), 3)
    active = next(provider for provider in providers if provider.calls)
    active.close_error = True
    task = service.contexts.tasks["fixture-maintenance"]
    release.set()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 30)
    assert service.store.all("SELECT * FROM direct_context_reservations") == []
    assert not service.contexts.tasks and not service.contexts.cancellations
    assert not service.store.reserved(project["root"])


async def test_manual_operation_reserves_overlap_and_blocks_account_mutation(context_service):
    service, project, session, release, started, _ = context_service
    await service.dispatch("session.context.compact", params(project, session))
    await asyncio.wait_for(started.wait(), 3)
    assert service.direct_accounts.busy()
    with pytest.raises(KiraError, match="active"):
        await service.dispatch("session.configure", {"project_id": project["id"], "session_id": session["id"], "model": "other-model"})
    with pytest.raises(KiraError, match="Active"):
        await service.dispatch("service.shutdown", {})
    with pytest.raises(KiraError) as settings_error:
        await service.dispatch("settings.update", {"tool_timeout": 30})
    assert settings_error.value.code == "BUSY"
    with pytest.raises(KiraError) as pin_error:
        await service.dispatch("session.context.pin", {**params(project, session, "different-intent"), "content": "New fixture note"})
    assert pin_error.value.code == "BUSY"
    assert not service.contexts.data.pins(session["id"])
    release.set()


async def test_shutdown_cancellation_during_client_close_cleans_reservation(context_service, monkeypatch):
    service, project, session, release, started, providers = context_service
    await service.dispatch("session.context.compact", params(project, session))
    await asyncio.wait_for(started.wait(), 3)
    active = next(provider for provider in providers if provider.calls)
    cleanup_entered = asyncio.Event()

    async def waiting_close():
        cleanup_entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(active, "close", waiting_close)
    task = service.contexts.tasks["fixture-maintenance"]
    release.set()
    await asyncio.wait_for(cleanup_entered.wait(), 3)
    task.cancel()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 30)
    assert service.store.all("SELECT * FROM direct_context_reservations") == []
    assert not service.contexts.tasks and not service.store.reserved(project["root"])
