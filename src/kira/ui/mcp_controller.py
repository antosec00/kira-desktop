"""Qt controller for managing external MCP servers in Kira Settings."""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from kira.ui.presentation import refresh_ui, tr
from kira.ui.qt_properties import qt_property
from kira.ui.service_errors import service_error_text

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]


def _parse_key_values(raw: str, *, allow_colon: bool = False) -> dict[str, str]:
    text = raw.strip()
    if not text:
        return {}
    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError(tr("Il formato JSON deve essere un oggetto chiave-valore."))
        return {str(k).strip(): str(v) for k, v in parsed.items() if str(k).strip()}
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sep = "=" if "=" in stripped else (":" if allow_colon and ":" in stripped else "")
        if not sep:
            raise ValueError(tr("Usa il formato CHIAVE=VALORE (una variabile per riga)."))
        key, val = stripped.split(sep, 1)
        key = key.strip()
        val = val.strip()
        if not key:
            raise ValueError(tr("Nome di variabile o header vuoto."))
        result[key] = val
    return result


class McpController(QObject):
    changed = Signal()
    importSucceeded = Signal()
    saveSucceeded = Signal()

    def __init__(self, workspace: Workspace) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self._servers: list[Json] = []
        self._busy = False
        self._testing_id = ""
        self._error = ""
        self._status_message = ""
        self._generation = 0
        workspace.changed.connect(self.changed.emit)
        workspace.teams.changed.connect(self.changed.emit)
        workspace.client.disconnected.connect(self._reset)

    def _reset(self) -> None:
        self._busy = False
        self._testing_id = ""
        self._generation += 1
        self.changed.emit()

    def _enrich(self, row: Json) -> Json:
        tools = row.get("tools", []) if isinstance(row.get("tools"), list) else []
        tool_names = [str(t.get("name", "")) for t in tools if isinstance(t, dict) and t.get("name")]
        status = str(row.get("status") or "untested")
        if status == "ready":
            status_label = tr("Connesso · {count} strumenti", count=len(tool_names))
        elif status == "error":
            status_label = tr("Errore di connessione")
        else:
            status_label = tr("Non ancora verificato")
        args_list = [str(a) for a in row.get("args", [])] if isinstance(row.get("args"), list) else []
        env_keys = [str(k) for k in row.get("env_keys", [])] if isinstance(row.get("env_keys"), list) else []
        header_keys = [str(k) for k in row.get("header_keys", [])] if isinstance(row.get("header_keys"), list) else []
        endpoint_summary = (
            (str(row.get("command", "")) + (" " + shlex.join(args_list) if args_list else "")).strip()
            if row.get("transport") == "stdio"
            else str(row.get("url", ""))
        )
        return {
            "id": str(row.get("id", "")),
            "name": str(row.get("name", "")),
            "transport": str(row.get("transport", "stdio")),
            "transportLabel": "stdio" if row.get("transport") == "stdio" else "HTTP / SSE",
            "enabled": bool(row.get("enabled", True)),
            "read_only": bool(row.get("read_only", False)),
            "timeout_seconds": int(row.get("timeout_seconds", 30)),
            "command": str(row.get("command", "")),
            "args": args_list,
            "argsText": shlex.join(args_list) if args_list else "",
            "cwd": str(row.get("cwd", "")),
            "url": str(row.get("url", "")),
            "endpointSummary": endpoint_summary,
            "envKeys": env_keys,
            "envKeysText": ", ".join(env_keys),
            "headerKeys": header_keys,
            "headerKeysText": ", ".join(header_keys),
            "status": status,
            "statusLabel": status_label,
            "lastError": str(refresh_ui(row.get("last_error", ""))),
            "toolCount": len(tool_names),
            "toolsText": ", ".join(tool_names[:20]) + ("…" if len(tool_names) > 20 else ""),
        }

    def _apply_servers(self, result: Json) -> None:
        raw_list = result.get("servers", [])
        if isinstance(raw_list, list):
            self._servers = [self._enrich(item) for item in raw_list if isinstance(item, dict)]

    @qt_property("QVariantList", notify=changed)
    def servers(self) -> list[Json]:
        return [dict(item) for item in self._servers]

    @qt_property(int, notify=changed)
    def count(self) -> int:
        return len(self._servers)

    @qt_property(int, notify=changed)
    def enabledCount(self) -> int:
        return sum(1 for item in self._servers if item.get("enabled"))

    @qt_property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @qt_property(str, notify=changed)
    def testingServerId(self) -> str:
        return self._testing_id

    @qt_property(bool, notify=changed)
    def editable(self) -> bool:
        return bool(
            self.workspace.connected
            and not self.workspace.busy
            and not self.workspace.teams.busy
            and not self._busy
        )

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return str(refresh_ui(self._error))

    @qt_property(str, notify=changed)
    def statusMessage(self) -> str:
        return str(refresh_ui(self._status_message))

    @Slot()
    def clearMessages(self) -> None:
        if self._error or self._status_message:
            self._error = ""
            self._status_message = ""
            self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        if not self.workspace.connected:
            return
        generation = self._generation

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._apply_servers(result)
            self.changed.emit()

        def failed(_error: Json) -> None:
            if generation != self._generation:
                return
            self.changed.emit()

        self.workspace.client.request("mcp.servers.list", {}, done, failed)

    @Slot(str)
    def importJson(self, raw_json: str) -> None:
        if not self.editable:
            return
        if not raw_json.strip():
            self._error = tr("Incolla una configurazione JSON mcpServers valida.")
            self.changed.emit()
            return
        self._busy = True
        self._error = ""
        self._status_message = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._apply_servers(result)
            imported = int(result.get("imported", 0))
            self._status_message = tr("Importati {count} server MCP.", count=imported)
            self.importSucceeded.emit()
            self.changed.emit()

        def failed(err: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, tr("Impossibile importare la configurazione MCP."))
            self.changed.emit()

        self.workspace.client.request("mcp.servers.import", {"json": raw_json}, done, failed)

    @Slot(str, str, str, str, str, str, bool)
    def saveServer(
        self,
        server_id: str,
        name: str,
        transport: str,
        endpoint: str,
        args_text: str,
        secrets_text: str,
        read_only: bool,
    ) -> None:
        if not self.editable:
            return
        clean_name = name.strip()
        clean_endpoint = endpoint.strip()
        if not clean_name or not clean_endpoint:
            self._error = tr("Inserisci il nome del server e il comando o URL.")
            self.changed.emit()
            return
        params: Json = {
            "name": clean_name,
            "transport": "http" if transport == "http" else "stdio",
            "read_only": bool(read_only),
            "enabled": True,
        }
        if server_id.strip():
            params["id"] = server_id.strip()
        try:
            if params["transport"] == "stdio":
                params["command"] = clean_endpoint
                params["args"] = shlex.split(args_text.strip()) if args_text.strip() else []
                if secrets_text.strip():
                    params["env"] = _parse_key_values(secrets_text, allow_colon=False)
            else:
                params["url"] = clean_endpoint
                if secrets_text.strip():
                    params["headers"] = _parse_key_values(secrets_text, allow_colon=True)
        except ValueError as exc:
            self._error = str(exc)
            self.changed.emit()
            return

        self._busy = True
        self._error = ""
        self._status_message = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._apply_servers(result)
            self._status_message = tr("Server MCP salvato.")
            self.saveSucceeded.emit()
            self.changed.emit()

        def failed(err: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, tr("Impossibile salvare il server MCP."))
            self.changed.emit()

        self.workspace.client.request("mcp.servers.save", params, done, failed)

    @Slot(str, bool)
    def toggleEnabled(self, server_id: str, enabled: bool) -> None:
        if not self.editable:
            return
        self._busy = True
        self._error = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._apply_servers(result)
            self.changed.emit()

        def failed(err: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, tr("Impossibile aggiornare lo stato del server MCP."))
            self.changed.emit()

        self.workspace.client.request(
            "mcp.servers.toggle", {"server_id": server_id, "enabled": bool(enabled)}, done, failed
        )

    @Slot(str, bool)
    def toggleReadOnly(self, server_id: str, read_only: bool) -> None:
        if not self.editable:
            return
        self._busy = True
        self._error = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._apply_servers(result)
            self.changed.emit()

        def failed(err: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, tr("Impossibile aggiornare i permessi del server MCP."))
            self.changed.emit()

        self.workspace.client.request(
            "mcp.servers.toggle", {"server_id": server_id, "read_only": bool(read_only)}, done, failed
        )

    @Slot(str)
    def testServer(self, server_id: str) -> None:
        if not self.editable:
            return
        self._busy = True
        self._testing_id = server_id
        self._error = ""
        self._status_message = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._testing_id = ""
            self._apply_servers(result)
            if result.get("ok"):
                count = len(result.get("tools", []))
                self._status_message = tr("Connessione MCP riuscita · {count} strumenti rilevati.", count=count)
            else:
                self._error = str(result.get("error") or tr("Connessione al server MCP non riuscita."))
            self.changed.emit()

        def failed(err: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._testing_id = ""
            self._error = service_error_text(err, tr("Verifica del server MCP non riuscita."))
            self.changed.emit()

        self.workspace.client.request("mcp.servers.test", {"server_id": server_id}, done, failed)

    @Slot(str)
    def deleteServer(self, server_id: str) -> None:
        if not self.editable:
            return
        self._busy = True
        self._error = ""
        self._status_message = ""
        generation = self._generation
        self.changed.emit()

        def done(result: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._apply_servers(result)
            self._status_message = tr("Server MCP rimosso.")
            self.changed.emit()

        def failed(err: Json) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._error = service_error_text(err, tr("Impossibile rimuovere il server MCP."))
            self.changed.emit()

        self.workspace.client.request("mcp.servers.delete", {"server_id": server_id}, done, failed)
