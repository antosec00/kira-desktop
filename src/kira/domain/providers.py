"""Provider capability sets shared by the service and the GUI.

Key provider-specific behaviour on these sets instead of repeating literal
provider lists: a provider missing from one copy silently loses a feature.
"""

#: Providers whose turns run inside the service process over HTTP. They own no
#: provider process, so their work ends when the service stops.
IN_PROCESS_BACKENDS = frozenset({"ollama", "openai_chatgpt", "deepseek_account", "deepseek_web",
                                 "google_cloud"})

#: Providers that receive Kira's isolated browser and Computer tools.
WEB_BACKENDS = frozenset({"openai_chatgpt", "ollama", "deepseek_account", "deepseek_web",
                          "claude_code", "google_cloud"})

#: Providers that can run Team members, and therefore be offered Team delegation.
TEAM_BACKENDS = frozenset({"codex", "openai_chatgpt", "ollama", "deepseek_account",
                           "google_cloud"})

