"""Background TCP client; Qt signals carry all server messages to the UI."""

import socket
import threading
import uuid

from PySide6.QtCore import QObject, Signal

from shared.protocol import MessageBuffer, ProtocolError, encode_message


class NetworkClient(QObject):
    """One TCP connection with a reader thread and thread-safe send method."""

    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(
        self,
        address: str,
        port: int,
        entry_message_type: str,
        entry_payload: dict,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.address = address
        self.port = port
        self.entry_message_type = entry_message_type
        self.entry_payload = entry_payload
        self._stop = threading.Event()
        self._socket_lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("network client has already been started")
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def send_message(self, message_type: str, payload: dict, request_id: str | None = None) -> bool:
        """Send a protocol message safely from the GUI thread."""
        try:
            data = encode_message(message_type, request_id, payload)
            with self._socket_lock:
                if self._socket is None or self._stop.is_set():
                    return False
                self._socket.sendall(data)
            return True
        except (OSError, ProtocolError):
            return False

    def send_drawing_operation(self, operation_id: str, payload: dict) -> bool:
        return self.send_message("drawing_operation", payload, operation_id)

    def send_cursor_position(self, x: float, y: float) -> bool:
        return self.send_message("cursor_update", {"x": x, "y": y})

    def close(self) -> None:
        self._stop.set()
        with self._socket_lock:
            connection = self._socket
            self._socket = None
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

    def _read_loop(self) -> None:
        reason = "server disconnected"
        try:
            connection = socket.create_connection(
                (self.address, self.port), timeout=5
            )
            connection.settimeout(None)
            with self._socket_lock:
                if self._stop.is_set():
                    connection.close()
                    return
                self._socket = connection

            self.send_message(
                self.entry_message_type,
                self.entry_payload,
                request_id=uuid.uuid4().hex,
            )
            message_buffer = MessageBuffer()
            while not self._stop.is_set():
                chunk = connection.recv(4096)
                if not chunk:
                    break
                for message in message_buffer.feed(chunk):
                    self.message_received.emit(message)
        except (OSError, ProtocolError) as exc:
            reason = str(exc)
        finally:
            stop_was_requested = self._stop.is_set()
            self.close()
            if not stop_was_requested:
                self.disconnected.emit(reason)
