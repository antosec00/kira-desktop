"""Tests for Cost Telemetry, Prompt Caching stats, Context Window gauge, Token Budgeting, and Sub-Agent Waterfall Timeline."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject
from PySide6.QtQuick import QQuickItem

from kira.domain.paths import Paths
from kira.domain.pricing import (
    calculate_cost_telemetry,
    estimate_context_gauge,
    evaluate_budget,
    resolve_model_pricing,
)
from kira.service.storage import Store, new_id, now
from kira.service.team_store import TeamStore
from kira.ui.app import create_engine
from kira.ui.controller import Workspace


def test_pricing_and_prompt_caching_savings_for_anthropic_deepseek_openai_and_ollama():
    # Anthropic Claude Sonnet 4.6: $3/M input, $0.30/M cached (90% discount), $15/M output
    usage_claude = {
        "source": "anthropic_api",
        "model": "claude-sonnet-4-6",
        "input_tokens": 100_000,
        "cached_input_tokens": 90_000,
        "output_tokens": 2_000,
        "total_tokens": 102_000,
    }
    res = calculate_cost_telemetry(usage_claude)
    assert res["billing_mode"] == "api"
    # 10k uncached * $3/M ($0.03) + 90k cached * $0.30/M ($0.027) + 2k output * $15/M ($0.03) = $0.087
    assert res["total_cost_usd"] == pytest.approx(0.087, rel=1e-4)
    # Without cache: 100k * $3/M ($0.30) + 2k * $15/M ($0.03) = $0.33 -> saved $0.243
    assert res["cache_savings_usd"] == pytest.approx(0.243, rel=1e-4)
    assert res["cache_hit_percent"] == pytest.approx(90.0)

    # DeepSeek Web subscription: subscription mode with cache discount on deepseek-chat
    p_ds = resolve_model_pricing("deepseek_web", "deepseek-chat")
    assert p_ds.billing_mode == "subscription"
    assert p_ds.cached_input_per_m < p_ds.input_per_m

    # Local Ollama: $0.00 cost
    res_local = calculate_cost_telemetry(
        {"input_tokens": 50_000, "output_tokens": 10_000},
        backend="ollama",
        model="qwen2.5-coder:32b",
    )
    assert res_local["billing_mode"] == "local"
    assert res_local["total_cost_usd"] == 0.0


def test_context_window_gauge_thresholds_and_draft_estimation():
    gauge_normal = estimate_context_gauge(
        {"last_request": {"input_tokens": 24_000}},
        backend="openai_api",
        model="gpt-5.4",
        draft_text="Hello world " * 100,
    )
    assert gauge_normal["level"] == "normal"
    assert gauge_normal["draft_tokens"] > 0
    assert 0 < gauge_normal["fill_percent"] < 65.0

    gauge_warn = estimate_context_gauge(
        {"last_request": {"input_tokens": 150_000}},
        backend="anthropic_api",
        model="claude-sonnet-4-6",
        catalog_window=200_000,
    )
    assert gauge_warn["level"] == "warning"
    assert gauge_warn["fill_percent"] == pytest.approx(75.0)

    gauge_crit = estimate_context_gauge(
        {"last_request": {"input_tokens": 180_000}},
        backend="anthropic_api",
        model="claude-sonnet-4-6",
        draft_text="x" * 8000,
        catalog_window=200_000,
    )
    assert gauge_crit["level"] == "critical"
    assert gauge_crit["fill_percent"] >= 85.0


def test_budget_evaluation_for_tokens_and_usd():
    ok = evaluate_budget(50_000, 0.40, token_budget=100_000, cost_budget_usd=1.0)
    assert ok["active"] and not ok["exceeded"] and not ok["warning"]

    warn = evaluate_budget(85_000, 0.40, token_budget=100_000, cost_budget_usd=1.0)
    assert warn["active"] and warn["warning"] and not warn["exceeded"]

    exceeded = evaluate_budget(60_000, 1.05, token_budget=100_000, cost_budget_usd=1.0)
    assert exceeded["active"] and exceeded["exceeded"] and exceeded["cost_exceeded"]


def test_team_store_waterfall_telemetry_and_tool_spans(tmp_path):
    store = Store(tmp_path / "kira.sqlite")
    teams_db = TeamStore(store, lambda _event: None)
    project = {
        "id": "proj-1",
        "name": "Telemetry Project",
        "root": str(tmp_path),
        "permission": "project_write",
    }
    ts = now()
    store.db.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
        (project["id"], project["name"], project["root"], "", project["permission"], "{}", ts, ts),
    )

    config = {
        "request_id": new_id(),
        "goal": "Inspect performance and caching",
        "mode": "manual",
        "backend": "claude_code",
        "model": "claude-sonnet-4-6",
        "settings": {"token_budget": 100_000, "cost_budget_usd": 0.50},
        "round_limit": 3,
        "concurrency": 2,
        "max_agents": 2,
    }
    members = [
        {"id": "coordinator", "name": "Coordinator", "role": "Lead", "instructions": "", "access": "read"},
        {"id": "agent_1", "name": "Profiler", "role": "Analyze spans", "instructions": "", "access": "read"},
    ]
    team = teams_db.create(project, config, "fp-1", members)
    member_row = teams_db.member(team["id"], "agent_1")
    run, _ = store.create_run(member_row["session_id"], new_id(), "Run profiler")
    run_id = run["id"]
    store.db.execute(
        "INSERT INTO team_steps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (team["id"], 1, "step_1", "agent_1", "Profile SQLite queries", "Run profiler", "read", "[]", "completed", run_id, None, None, ts, ts),
    )
    store.db.execute(
        "INSERT INTO team_run_links VALUES (?,?,?,?,?,?)",
        (run_id, team["id"], "agent_1", 1, "step_1", 0),
    )
    store.event(run_id, "run.state", {"state": "running"})
    store.event(run_id, "tool.started", {"tool_id": "t1", "name": "read_file"})
    store.event(run_id, "tool.completed", {"tool_id": "t1", "name": "read_file", "output": "ok"})
    store.event(
        run_id,
        "usage",
        {
            "source": "claude_code",
            "model": "claude-sonnet-4-6",
            "input_tokens": 40_000,
            "cached_input_tokens": 30_000,
            "output_tokens": 1_500,
            "total_tokens": 41_500,
        },
    )
    store.event(run_id, "run.state", {"state": "completed"})

    telemetry = teams_db.telemetry(team["id"])
    assert telemetry["total_tokens"] == 41_500
    assert telemetry["cached_input_tokens"] == 30_000
    assert telemetry["cache_hit_percent"] == pytest.approx(75.0)
    assert telemetry["total_cost_usd"] > 0
    assert telemetry["cache_savings_usd"] > 0
    assert len(telemetry["spans"]) == 1
    span = telemetry["spans"][0]
    assert span["member_id"] == "agent_1"
    assert span["member_name"] == "Profiler"
    assert span["duration_ms"] >= 0
    assert len(span["tool_spans"]) == 1
    assert span["tool_spans"][0]["name"] == "read_file"
    assert span["tool_spans"][0]["status"] == "completed"
    assert telemetry["budget"]["active"] and not telemetry["budget"]["exceeded"]
    store.db.close()


def test_qml_context_window_gauge_and_usage_dialog_telemetry(qapp, qtbot, tmp_path):
    paths = Paths(*(tmp_path / name for name in ("config", "data", "state", "runtime")))
    workspace = Workspace(paths)
    workspace._backend = "claude_code"
    workspace._model = "claude-sonnet-4-6"
    workspace._usage = {
        "source": "claude_code",
        "model": "claude-sonnet-4-6",
        "requests": 2,
        "input_tokens": 100_000,
        "cached_input_tokens": 80_000,
        "output_tokens": 2_000,
        "total_tokens": 102_000,
        "last_request": {
            "source": "claude_code",
            "model": "claude-sonnet-4-6",
            "input_tokens": 50_000,
            "cached_input_tokens": 40_000,
            "output_tokens": 1_000,
            "total_tokens": 51_000,
        },
    }
    engine = create_engine(workspace)
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        gauge = window.findChild(QQuickItem, "contextWindowGauge")
        assert gauge is not None
        label = window.findChild(QQuickItem, "contextWindowGaugeLabel")
        assert label is not None
        assert "%" in label.property("text")

        # Update cost & token budgets and verify UsageDialog cards
        workspace.setCostBudgetUsd(2.0)
        workspace.setTokenBudget(500_000)
        assert workspace.costBudgetUsd == pytest.approx(2.0)
        assert workspace.tokenBudget == 500_000

        usage_dialog = window.findChild(QObject, "usageDialog")
        assert usage_dialog is not None
        QMetaObject.invokeMethod(usage_dialog, "open")
        qtbot.waitUntil(lambda: usage_dialog.property("opened"))
        for card_name in ("usageCostCard", "usagePromptCachingCard", "usageBudgetCard"):
            card = window.findChild(QQuickItem, card_name)
            assert card is not None and card.isVisible()
        QMetaObject.invokeMethod(usage_dialog, "close")
    finally:
        window.setProperty("closeConfirmed", True)
        window.close()
        engine.deleteLater()
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
