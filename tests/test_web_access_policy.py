"""Current tool availability controls guidance without rewriting user requests.

These synthetic tests check the request boundary, not a model's tool-use decision.
Natural-language tool use is qualified separately against the actual provider.
"""

import copy
import json

import httpx
import pytest

from kira.backends import openai_chatgpt
from kira.domain.web import browser_tool_specs
from test_backends_openai_chatgpt import FakeAuth, context, terminal, wire


@pytest.mark.parametrize("browser,search", [(False, False), (True, False), (False, True), (True, True)])
async def test_guidance_tracks_registered_web_capabilities_without_forcing_network(tmp_path, monkeypatch, browser, search):
    ctx, _, _ = context(tmp_path)
    ctx.session["settings"]["web_search"] = "live" if search else "disabled"
    ctx.prompt = "Senza aprire https://example.com/, spiegami solo il formato dell'indirizzo."
    ctx.history = [{"role": "assistant", "content": "Synthetic outdated claim: I cannot access web pages."}]
    original_history = copy.deepcopy(ctx.history)
    if browser:
        ctx.client_tools = browser_tool_specs()

        async def forbidden(*_):
            raise AssertionError("A URL in the prompt must not cause automatic local browsing")

        ctx.client_tool_call = forbidden
    observed = []

    def policy(**capabilities):
        observed.append(capabilities)
        return "\nCurrent capability policy fixture"

    monkeypatch.setattr(openai_chatgpt, "web_access_instructions", policy)
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["input"][-1] == {"role": "user", "content": ctx.prompt}
        assert ctx.history[0] in body["input"]
        assert body.get("tool_choice") is None
        assert body["instructions"].count("Current capability policy fixture") == 1
        return wire([terminal()])

    backend = openai_chatgpt.OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    try:
        assert (await backend.run(ctx))["state"] == "completed"
        assert observed == [{"browser": browser, "search": search}]
        assert len(requests) == 1 and ctx.history == original_history
    finally:
        await backend.close()
