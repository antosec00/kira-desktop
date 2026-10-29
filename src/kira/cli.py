"""Stable GUI and diagnostic command entry point."""

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

from kira import __version__
from kira.domain.contracts import KiraError

DOCTOR_BACKENDS = ("ollama", "codex", "openai_chatgpt", "deepseek_account", "deepseek_web",
                   "claude_code")


def error_report(exc: BaseException) -> dict[str, Any]:
    """A stable machine-readable error; an exception without text still says what failed."""
    if isinstance(exc, KiraError):
        code, fallback = exc.code, "The Kira service rejected the request"
    elif isinstance(exc, TimeoutError):
        code, fallback = "TIMEOUT", "The Kira service did not answer in time"
    elif isinstance(exc, (ConnectionError, FileNotFoundError)):
        code, fallback = "UNAVAILABLE", "The Kira service is not reachable"
    elif isinstance(exc, PermissionError):
        code, fallback = "PERMISSION_DENIED", "Permission denied"
    elif isinstance(exc, (ValueError, TypeError)):
        code, fallback = "INVALID_ARGUMENT", "Invalid value"
    else:
        code, fallback = "FAILED", "The command failed"
    return {"code": code, "message": str(exc) or fallback}


def print_error(exc: BaseException) -> int:
    print(json.dumps({"error": error_report(exc)}, ensure_ascii=False), file=sys.stderr)
    return 1


async def stop_service() -> dict[str, Any]:
    """Ask a running service of any version to stop; never start one to stop it."""
    from kira.client import Client

    client = Client()
    try:
        hello = await client.connect(same_version=False)
    except (FileNotFoundError, ConnectionRefusedError):
        return {"stopping": False, "running": False, "message": "Kira service is not running"}
    try:
        result = dict(await client.request("service.shutdown"))
    finally:
        await client.close()
    if hello.get("app_version") != __version__:
        result["service_version"] = hello.get("app_version")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kira-desktop")
    parser.add_argument("--version", action="version", version=f"Kira Desktop {__version__}")
    parser.add_argument(
        "--spotlight",
        action="store_true",
        help="Toggle or open the Ambient Spotlight overlay over the active desktop window",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("service", help="Run the authoritative service in the foreground")
    sub.add_parser("spotlight", help="Toggle or open the Ambient Spotlight overlay")
    rpc = sub.add_parser("rpc", help="Send a v1 IPC request (JSON output)")
    rpc.add_argument("method")
    rpc.add_argument("params", nargs="?", default="{}")
    sub.add_parser("doctor", help="Report real service, provider and resource status")
    diagnostics = sub.add_parser("diagnostics", help="Show privacy-filtered local diagnostics")
    diagnostics.add_argument("--export", action="store_true", help="Save a private JSON report")
    actions = sub.add_parser("actions", help="List explicit native desktop actions")
    actions.add_argument("query", nargs="?", default="")
    actions.add_argument("--project", help="Project identifier for project-specific actions")
    workspace = sub.add_parser("workspace", help="Inspect or explicitly open a saved project workspace")
    workspace.add_argument("project_id")
    workspace.add_argument("--open", action="store_true", help="Request the saved native tools")
    workspace.add_argument("--request-id", help="Reuse an explicit ID to reconcile a prior open request")
    sub.add_parser("stop-service", help="Stop an idle service safely before an upgrade, "
                   "including a service of another version")
    sub.add_parser("browser-install", help="Download Kira's isolated Chromium engine for the selected profile")
    args = parser.parse_args(argv)
    if args.command == "browser-install":
        from kira.tools.browser_install import install_browser
        try:
            return install_browser()
        except (KiraError, OSError, RuntimeError, ValueError) as exc:
            return print_error(exc)
    if args.command == "service":
        from kira.service.server import main as service_main
        service_main()
        return 0
    if args.spotlight or args.command == "spotlight":
        from kira.client import Client
        from kira.domain.ambient import inspect_active_window, read_os_primary_selection

        win_info = inspect_active_window()
        sel_text = read_os_primary_selection()

        async def _try_toggle_running_ui() -> int:
            client = Client()
            try:
                await client.connect()
            except (FileNotFoundError, ConnectionRefusedError, KiraError, OSError):
                return 0
            try:
                res = await client.request(
                    "ambient.toggle",
                    {
                        "app_name": win_info.get("app_name", ""),
                        "window_title": win_info.get("window_title", ""),
                        "selection": sel_text,
                    },
                )
                return int(res.get("delivered") or 0)
            except Exception:
                return 0
            finally:
                await client.close()

        delivered = asyncio.run(_try_toggle_running_ui())
        if delivered > 0:
            return 0
        from kira.ui.app import main as ui_main
        return ui_main(spotlight=True) or 0
    # Reject malformed arguments before any service is started.
    params: dict[str, Any] = {}
    if args.command == "rpc":
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as exc:
            rpc.error(f"params must be a JSON object: {exc.msg}")
        if not isinstance(params, dict):
            rpc.error("params must be a JSON object")
    if args.command == "workspace" and args.request_id and not args.open:
        workspace.error("--request-id requires --open")
    if args.command in {"rpc", "doctor", "stop-service", "diagnostics", "actions", "workspace"}:
        from kira.client import Client, ensure_service

        async def invoke() -> dict:
            if args.command == "stop-service":
                return await stop_service()
            await ensure_service()
            async with Client() as client:
                if args.command == "doctor":
                    report = {"service": await client.request("hello"),
                              "resources": await client.request("system.resources")}
                    for backend in DOCTOR_BACKENDS:
                        # One unavailable provider must not hide the others.
                        try:
                            report[backend] = await client.request("backend.discover",
                                                                   {"backend": backend})
                        except (KiraError, OSError) as exc:
                            report[backend] = {"available": False, "error": error_report(exc)}
                    return report
                if args.command == "diagnostics":
                    return await client.request("diagnostics.export" if args.export
                                                else "system.diagnostics")
                if args.command == "actions":
                    parameters = {"query": args.query}
                    if args.project:
                        parameters["project_id"] = args.project
                    return await client.request("desktop.actions", parameters)
                if args.command == "workspace":
                    parameters = {"project_id": args.project_id}
                    if args.open:
                        parameters["request_id"] = args.request_id or str(uuid.uuid4())
                    return await client.request("workspace.launch" if args.open else "workspace.get",
                                                parameters)
                return await client.request(args.method, params)

        try:
            print(json.dumps(asyncio.run(invoke()), indent=2, ensure_ascii=False))
        except Exception as exc:
            return print_error(exc)
        return 0
    from kira.ui.app import main as ui_main
    return ui_main() or 0


if __name__ == "__main__":
    raise SystemExit(main())
