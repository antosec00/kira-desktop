"""Team mailbox, stop and recovery paths with the scheduler fixtures; no model inference."""

from kira.domain.teams import MAX_TEAM_MESSAGE_BYTES
from kira.service.teams import Teams
from test_service_teams import assignment, env, finished, member, params, wait_event  # noqa: F401


async def test_one_heavily_escaped_message_cannot_block_a_mailbox(env):  # noqa: F811
    service, _, _ = env
    config, project, roster = service.teams._config(params(env))
    team = service.teams.data.create(project, config, "escaped-mailbox", roster)
    # Each control character takes six bytes once JSON-escaped: one message exceeds the page budget.
    content = "\x01" * MAX_TEAM_MESSAGE_BYTES
    service.teams._send(team["id"], "user", "User", "escaped", "user:" + team["id"],
                        {"recipient_id": "verifier", "content": content})
    service.teams._send(team["id"], "user", "User", "after", "user:" + team["id"],
                        {"recipient_id": "verifier", "content": "Next synthetic message"})
    first = service.teams.mailbox(team["id"], "verifier", 0, 20)
    assert [message["content"] for message in first["messages"]] == [content]
    assert first["has_more"] is True
    second = service.teams.mailbox(team["id"], "verifier", first["last_seq"], 20)
    assert [message["content"] for message in second["messages"]] == ["Next synthetic message"]
    assert second["has_more"] is False


async def test_stop_racing_a_scheduled_member_cancels_the_team(env, monkeypatch):  # noqa: F811
    service, factory, _ = env
    factory.roster = [member("coordinator"), member("first"), member("second")]
    factory.plan_tasks = [assignment("one", "first"), assignment("two", "second")]
    original = Teams._member_run

    async def racing(self, team_id, member_id, prompt, *, coordinator, step=None):
        if step is not None and step["id"] == "two":
            # The user stops the Team after this member was scheduled, before it starts.
            await wait_event(factory, "started", "first")
            await service.dispatch("team.cancel", {"team_id": team_id})
        return await original(self, team_id, member_id, prompt, coordinator=coordinator, step=step)

    monkeypatch.setattr(Teams, "_member_run", racing)
    team = (await service.dispatch("team.start", params(env)))["team"]
    final = await finished(service, team["id"])
    assert final["state"] == "cancelled", final
    assert "Service stopped" not in (final["error"] or "")
    assert "second" not in factory.started
    assert next(step for step in final["steps"] if step["id"] == "two")["state"] == "cancelled"
    assert not service.store.all("SELECT * FROM project_reservations")


async def test_recovery_skips_settled_teams_and_releases_retained_work(env, monkeypatch):  # noqa: F811
    service, _, _ = env
    settled = (await service.dispatch("team.start", params(env)))["team"]
    assert (await finished(service, settled["id"]))["state"] == "completed"
    config, project, roster = service.teams._config(params(env, request_id="retained-reservation"))
    retained = service.teams.data.create(project, config, "retained-reservation", roster)
    service.teams.data.state(retained["id"], "interrupted", phase="cleanup_blocked")
    checked = []
    original = service.ensure_run_cleanup

    async def tracked(run_id):
        checked.append(run_id)
        return await original(run_id)

    monkeypatch.setattr(service, "ensure_run_cleanup", tracked)
    await service.teams.recover()
    settled_runs = {row["run_id"] for row in service.store.all(
        "SELECT run_id FROM team_run_links WHERE team_id=?", (settled["id"],))}
    assert settled_runs and not settled_runs & set(checked)
    assert not service.store.all("SELECT * FROM project_reservations WHERE owner_id=?", (retained["id"],))
    assert service.teams.data.one(settled["id"])["state"] == "completed"
