"""Effective Kira tool availability shared by service-owned callbacks."""

from kira.domain.contracts import Json


def kira_tools_enabled(session: Json) -> bool:
    """Return whether this provider's Kira callbacks are enabled for a chat.

    DeepSeek account and web runs deliberately normalize the legacy Ollama-only
    ``tools=false`` preference to true before building their Kira tool loop.
    Callback admission must use the same effective policy even when it reloads
    the original persisted session row.
    """
    return (session.get("backend") in {"deepseek_account", "deepseek_web", "google_cloud"}
            or session.get("settings", {}).get("tools", True) is not False)
