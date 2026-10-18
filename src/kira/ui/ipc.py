"""Non-blocking Qt transport for Kira's private local IPC protocol."""

import json
import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from kira.ui.presentation import tr

from kira import __version__
from kira.domain.contracts import IPC_VERSION, MAX_FRAME_BYTES

Json = dict[str, Any]
Callback = Callable[[Json], None]


class LocalClient(QObject):
    ready = Signal()
    disconnected = Signal()
    eventReceived = Signal(dict)
    teamEvent = Signal(dict)
    browserEventReceived = Signal(dict)
    problem = Signal(object)

    def __init__(self, socket_path: str, parent: QObject | None = None):
        super().__init__(parent)
        self.socket_path = socket_path
        self.socket = QLocalSocket(self)
        self.socket.setReadBufferSize(MAX_FRAME_BYTES + 1)
        self.socket.connected.connect(self._hello)
        self.socket.readyRead.connect(self._read)
        self.socket.disconnected.connect(self._disconnected)
        self.socket.errorOccurred.connect(self._error)
        self._buffer = bytearray()
        self._pending: dict[str, tuple[Callback, Callback | None]] = {}
        self.compatible = False
        self.incompatible = False
        self._retry = QTimer(self)
        self._retry.setInterval(1800)
        self._retry.timeout.connect(self.connect_service)

    def connect_service(self) -> None:
        if self.incompatible:
            return
        if self.socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            return
        self._buffer.clear()
        self.socket.connectToServer(self.socket_path)
        self._retry.start()

    def _hello(self) -> None:
        def checked(result: Json) -> None:
            if result.get("version") != IPC_VERSION or result.get("app_version") != __version__:
                self.incompatible = True
                self.problem.emit(
                    tr("Servizio incompatibile. Attendi la fine dei lavori, arresta il servizio "
                    "e riapri Kira dopo l'aggiornamento.")
                )
                self._retry.stop()
                self.socket.abort()
                return
            self.compatible = True
            self._retry.stop()
            self.ready.emit()

        self.request("hello", {}, checked, self._hello_error, handshake=True)

    def _hello_error(self, error: Json) -> None:
        if error.get("code") == "DISCONNECTED":
            return
        self.incompatible = True
        self._retry.stop()
        self.problem.emit(str(error.get("message", tr("Handshake non riuscito"))))
        self.socket.abort()

    def request(
        self,
        method: str,
        params: Json,
        callback: Callback,
        failed: Callback | None = None,
        *,
        handshake: bool = False,
    ) -> bool:
        if not self.compatible and not handshake:
            error = {"code": "DISCONNECTED", "message": tr("Connessione al servizio in corso.")}
            if failed:
                failed(error)
            else:
                self.problem.emit(error["message"])
            return False
        request_id = str(uuid.uuid4())
        payload = json.dumps(
            {"version": IPC_VERSION, "id": request_id, "method": method, "params": params},
            ensure_ascii=False,
        ).encode() + b"\n"
        if len(payload) > MAX_FRAME_BYTES:
            error = {"code": "FRAME_TOO_LARGE",
                     "message": tr("La richiesta supera il limite IPC di {limit} byte.", limit=MAX_FRAME_BYTES)}
            if failed:
                failed(error)
            else:
                self.problem.emit(error["message"])
            return False
        self._pending[request_id] = (callback, failed)
        self.socket.write(payload)
        return True

    def _read(self) -> None:
        self._buffer.extend(self.socket.readAll().data())
        while b"\n" in self._buffer:
            line, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            if len(line) > MAX_FRAME_BYTES:
                self._bad_frame(tr("Risposta del servizio troppo grande."))
                return
            try:
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("version") != IPC_VERSION:
                    raise ValueError("Incompatible envelope")
                if "browser_event" in message:
                    if not isinstance(message["browser_event"], dict):
                        raise ValueError("Invalid browser event")
                    self.browserEventReceived.emit(message["browser_event"])
                elif "team_event" in message:
                    if not isinstance(message["team_event"], dict):
                        raise ValueError("Invalid team event")
                    self.teamEvent.emit(message["team_event"])
                elif "event" in message:
                    if not isinstance(message["event"], dict):
                        raise ValueError("Invalid event")
                    self.eventReceived.emit(message["event"])
                else:
                    callbacks = self._pending.pop(str(message.get("id")), None)
                    if callbacks is None:
                        continue
                    callback, failed = callbacks
                    if "error" in message:
                        if failed:
                            failed(message["error"])
                        else:
                            self.problem.emit(str(message["error"].get("message", tr("Errore"))))
                    else:
                        callback(message.get("result", {}))
            except (ValueError, TypeError, UnicodeDecodeError):
                self._bad_frame(tr("Risposta IPC non valida; riconnessione in corso."))
                return
        if len(self._buffer) > MAX_FRAME_BYTES:
            self._bad_frame(tr("Risposta del servizio troppo grande."))

    def _bad_frame(self, message: str) -> None:
        self.problem.emit(message)
        self.socket.abort()

    def _disconnected(self) -> None:
        self.compatible = False
        callbacks, self._pending = self._pending, {}
        for _, failed in callbacks.values():
            if failed:
                failed({"code": "DISCONNECTED", "message": tr("Connessione interrotta.")})
        self.disconnected.emit()
        if not self.incompatible:
            self._retry.start()

    def _error(self, _error: QLocalSocket.LocalSocketError) -> None:
        if not self.compatible and not self.incompatible:
            self.problem.emit(tr("In attesa del servizio locale…"))

    def close(self) -> None:
        self._retry.stop()
        self.incompatible = True
        self.socket.abort()
