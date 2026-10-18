"""Stable GUI and diagnostic command entry point."""

import argparse
import asyncio
import json
import uuid

from kira import __version__


def main() -> int:
    parser = argparse.ArgumentParser(prog="kira-desktop")
    parser.add_argument("--version", action="version", version=f"Kira Desktop {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("service", help="Run the authoritative service in the foreground")
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
    sub.add_parser("stop-service", help="Stop an idle service safely before an upgrade")
    sub.add_parser("browser-install", help="Download Kira's isolated Chromium engine for the selected profile")
    args = parser.parse_args()
    if args.command == "browser-install":
        from kira.tools.browser_install import install_browser
        return install_browser()
    if args.command == "service":
        from kira.service.server import main as service_main
        service_main()
        return 0
    if args.command in {"rpc", "doctor", "stop-service", "diagnostics", "actions", "workspace"}:
        from kira.client import Client, ensure_service

        async def invoke() -> dict:
            await ensure_service()
            async with Client() as client:
                if args.command == "doctor":
                    return {"service": await client.request("hello"),
                            "resources": await client.request("system.resources"),
                            "ollama": await client.request("backend.discover", {"backend": "ollama"}),
                            "codex": await client.request("backend.discover", {"backend": "codex"}),
                            "openai_chatgpt": await client.request("backend.discover", {"backend": "openai_chatgpt"})}
                if args.command == "stop-service":
                    return await client.request("service.shutdown")
                if args.command == "diagnostics":
                    return await client.request("diagnostics.export" if args.export
                                                else "system.diagnostics")
                if args.command == "actions":
                    params = {"query": args.query}
                    if args.project:
                        params["project_id"] = args.project
                    return await client.request("desktop.actions", params)
                if args.command == "workspace":
                    parameters = {"project_id": args.project_id}
                    if args.open:
                        parameters["request_id"] = args.request_id or str(uuid.uuid4())
                    elif args.request_id:
                        raise ValueError("--request-id requires --open")
                    return await client.request("workspace.launch" if args.open else "workspace.get",
                                                parameters)
                return await client.request(args.method, json.loads(args.params))

        try:
            print(json.dumps(asyncio.run(invoke()), indent=2, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
            return 1
        return 0
    from kira.ui.app import main as ui_main
    return ui_main() or 0


if __name__ == "__main__":
    raise SystemExit(main())
