"""Bundled guides describe the providers and permissions Kira actually applies."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from kira.backends.claude_code import MCP_SERVER_NAME, tool_set
from kira.backends.codex import permission_overlay
from kira.domain.computer import computer_tool_specs
from kira.domain.contracts import PERMISSIONS
from kira.service.knowledge import Knowledge
from kira.service.storage import Store, now

GUIDES = Path(__file__).resolve().parents[1] / "src/kira/resources/knowledge"
LABELS = {"read_only": "read-only", "project_write": "project-write",
          "trusted_host": "trusted-host", "full_access": "full-access"}


@pytest.fixture
def knowledge(tmp_path):
    store = Store(tmp_path / "knowledge.sqlite3")
    stamp = now()
    store.db.execute("INSERT INTO projects VALUES (?,?,?,'','project_write','{}',?,?)",
                     ("project", "Synthetic project", str(tmp_path / "project"), stamp, stamp))
    store.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL,?)",
                     ("chat", "project", "openai_chatgpt", "fixture-model", "{}", stamp))
    yield Knowledge(SimpleNamespace(store=store))
    store.close()


def guide(knowledge, identity):
    return " ".join(knowledge.read("project", "chat", "app:" + identity, limit=12000)["content"].split())


def test_every_bundled_guide_matches_its_indexed_revision(knowledge):
    entries = json.loads((GUIDES / "index.json").read_text(encoding="utf-8"))
    assert {entry["id"] + ".md" for entry in entries} == {path.name for path in GUIDES.glob("*.md")}
    for entry in entries:
        result = knowledge.read("project", "chat", "app:" + entry["id"])
        assert result["revision"] == entry["revision"]


def test_permissions_guide_states_the_codex_sandbox_of_each_permission(knowledge):
    content = guide(knowledge, "permissions")
    for permission in PERMISSIONS:
        sandbox, _ = permission_overlay(SimpleNamespace(permission=permission, root=Path("/project")))
        assert re.search(re.escape(LABELS[permission]) + r"[^.;:]*`" + re.escape(sandbox) + "`", content)
    assert "danger-full-access`, which is no sandbox" in content
    assert "must not be described as unrestricted host access" not in content


def test_provider_guide_covers_every_provider_and_claude_code_tool_policy(knowledge):
    content = guide(knowledge, "providers")
    for label in ("OpenAI direct", "Codex", "Ollama", "DeepSeek Account", "DeepSeek Web", "Claude Code"):
        assert label in content
    assert all(f"`{name}`" in content for name in tool_set("full_access"))
    assert f"`mcp__{MCP_SERVER_NAME}__<namespace>__<tool>`" in content
    assert "`dontAsk`" in content and "Kira's Computer is never offered" in content


@pytest.mark.parametrize("query,expected", [("deepseek", "app:providers"), ("claude", "app:providers"),
                                            ("anthropic", "app:providers"), ("codex sandbox", "app:permissions")])
def test_guides_are_discoverable_by_provider_names(knowledge, query, expected):
    found = knowledge.search("project", "chat", query, scope="app")["items"]
    assert expected in [item["ref"] for item in found]


def test_context_guide_includes_deepseek_account_managed_context(knowledge):
    assert "OpenAI direct and DeepSeek Account keep a durable Kira-managed working context" in guide(
        knowledge, "context")


def test_bundled_guides_describe_current_behaviour_without_release_history():
    for path in GUIDES.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(?:Kira|version|v)\s*\d+\.\d+|\b\d+\.\d+\.\d+\b", text), path.name
        assert "remains available" not in text, path.name


def test_computer_namespace_description_uses_the_english_product_name():
    namespace = computer_tool_specs()[0]
    assert namespace["description"].startswith("Kira's Computer: ")
    assert "Computer di Kira" not in json.dumps(computer_tool_specs(), ensure_ascii=False)
