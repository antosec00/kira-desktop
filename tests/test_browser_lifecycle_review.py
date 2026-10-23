"""Browser cancellation through the real direct-provider bridge, without a process."""

import asyncio
from types import SimpleNamespace

import httpx

from kira.backends.openai_chatgpt import OpenAIChatGPTBackend
from kira.domain.web import browser_tool_specs
from kira.tools.browser import BrowserSession
from test_backends_openai_chatgpt import FakeAuth, call, context, terminal, wire


async def test_slow_bounded_browser_shutdown_preserves_cancelled_provider_outcome(tmp_path):
    ctx, _, _ = context(tmp_path)
    entered = asyncio.Event()
    process_exited = asyncio.Event()
    stdin_closed = asyncio.Event()

    async def drain():
        return None

    async def process_wait():
        # Chromium's allowed orderly shutdown exceeds the generic client-tool
        # one-second cancellation deadline. No OS process or browser is started.
        await asyncio.sleep(1.1)
        process_exited.set()
        return 0

    async def forbidden_start(pid):
        raise AssertionError("This synthetic test must never start a process")

    worker = BrowserSession(tmp_path / "profile", tmp_path / "artifacts", headed=False,
                            persistent=False, started=forbidden_start)
    worker._process = SimpleNamespace(
        pid=12345, returncode=None,
        stdin=SimpleNamespace(write=lambda _: None, drain=drain, close=stdin_closed.set),
        stdout=asyncio.StreamReader(), wait=process_wait)

    async def callback(identity, name, arguments):
        assert name == "browser.snapshot"
        entered.set()
        return await worker.call("snapshot", {})

    ctx.client_tools = browser_tool_specs()
    ctx.client_tool_call = callback
    requests = []

    def handler(request):
        requests.append(request)
        return wire([terminal([call(name="snapshot", namespace="browser")])])

    backend = OpenAIChatGPTBackend(FakeAuth(), transport=httpx.MockTransport(handler))
    task = asyncio.create_task(backend.run(ctx))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        ctx.cancel.set()
        result = await asyncio.wait_for(task, 5)
        assert result["state"] == "cancelled", result
        assert stdin_closed.is_set() and process_exited.is_set()
        assert len(requests) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await backend.close()
