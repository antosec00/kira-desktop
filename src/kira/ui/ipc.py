"""Non-blocking Qt transport for Kira's private local IPC protocol."""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from kira.ui.presentation import tr
from kira.ui.service_errors import service_error_text

from kira import __version__
from kira.domain.contracts import IPC_VERSION, MAX_FRAME_BYTES

Json = dict[str, Any]
Callback = Callable[[Json], None]
LOGGER = logging.getLogger(__name__)
NOTIFICATIONS = ("browser_event", "team_event", "event")


class IncompatibleFrame(ValueError):
    """A well-formed frame from a service speaking another IPC version."""


def parse_frame(line: bytes) -> Json:
    """Validate one frame completely before any interface code sees it."""
    message = json.loads(line)
    if not isinstance(message, dict) or type(message.get("version")) is not int:
        raise ValueError("Invalid envelope")
    if message["version"] != IPC_VERSION:
        raise IncompatibleFrame("Incompatible IPC version")
    for key in NOTIFICATIONS:
        if key in message:
            if not isinstance(message[key], dict):
                raise ValueError("Invalid notification")
            return message
    if not isinstance(message.get("error", {}), dict) or not isinstance(message.get("result", {}), dict):
        raise ValueError("Invalid response")
    return message


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
        # Frames already buffered from a closed connection are never delivered.
        self._connection = 0
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

    def _refuse_incompatible(self) -> None:
        """Another version needs user action; reconnecting in a loop cannot fix it."""
        self.incompatible = True
        self._retry.stop()
        self.problem.emit(
            tr("Servizio incompatibile. Attendi la fine dei lavori, arresta il servizio "
               "e riapri Kira dopo l'aggiornamento.")
        )
        self.socket.abort()

    def _hello(self) -> None:
        def checked(result: Json) -> None:
            if result.get("version") != IPC_VERSION or result.get("app_version") != __version__:
                self._refuse_incompatible()
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
        connection = self._connection
        while b"\n" in self._buffer and connection == self._connection:
            line, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            if len(line) > MAX_FRAME_BYTES:
                self._bad_frame(tr("Risposta del servizio troppo grande."))
                return
            try:
                message = parse_frame(line)
            except IncompatibleFrame:
                self._refuse_incompatible()
                return
            except (ValueError, TypeError, RecursionError):
                self._bad_frame(tr("Risposta IPC non valida; riconnessione in corso."))
                return
            try:
                self._deliver(message)
            except Exception:
                # Interface code is not part of the protocol: its failure neither
                # drops the connection nor strands the frames already buffered.
                LOGGER.exception("Kira interface callback failed for an IPC frame")
        if connection == self._connection and len(self._buffer) > MAX_FRAME_BYTES:
            self._bad_frame(tr("Risposta del servizio troppo grande."))

    def _deliver(self, message: Json) -> None:
        if "browser_event" in message:
            self.browserEventReceived.emit(message["browser_event"])
        elif "team_event" in message:
            self.teamEvent.emit(message["team_event"])
        elif "event" in message:
            self.eventReceived.emit(message["event"])
        else:
            callbacks = self._pending.pop(str(message.get("id")), None)
            if callbacks is None:
                return
            callback, failed = callbacks
            if "error" in message:
                if failed:
                    failed(message["error"])
                else:
                    self.problem.emit(service_error_text(message["error"], tr("Errore")))
            else:
                callback(message.get("result", {}))

    def _bad_frame(self, message: str) -> None:
        self.problem.emit(message)
        self.socket.abort()

    def _disconnected(self) -> None:
        self._connection += 1
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
