"""Background TCP client; Qt signals carry all server messages to the UI."""

import socket
import threading
import time
import uuid

from PySide6.QtCore import QObject, Signal

from shared.protocol import MessageBuffer, ProtocolError, encode_message


class NetworkClient(QObject):
    """One TCP connection with a reader thread and thread-safe send method."""

    message_received = Signal(object)
    disconnected = Signal(str)
    reconnecting = Signal(str)

    def __init__(
        self,
        address: str,
        port: int,
        entry_message_type: str,
        entry_payload: dict,
        parent: QObject | None = None,
        heartbeat_interval: float = 5.0,
        heartbeat_timeout: float = 15.0,
        reconnect_delays: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0, 10.0),
    ) -> None:
        super().__init__(parent)
        self.address = address
        self.port = port
        self.entry_message_type = entry_message_type
        self.entry_payload = entry_payload
        self.heartbeat_interval = max(0.1, heartbeat_interval)
        self.heartbeat_timeout = max(self.heartbeat_interval * 2, heartbeat_timeout)
        self.reconnect_delays = reconnect_delays or (1.0,)
        self._room_code = entry_payload.get("room_code")
        self._username = entry_payload.get("username")
        self._resume_token = entry_payload.get("resume_token")
        self._has_joined = False
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

    def send_chat_message(self, text: str) -> bool:
        return self.send_message(
            "chat_message", {"text": text}, request_id=uuid.uuid4().hex
        )

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
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _read_loop(self) -> None:
        retry_index = 0
        last_reason = "connection lost"
        reported_disconnect = False
        while not self._stop.is_set():
            connection: socket.socket | None = None
            try:
                connection = socket.create_connection((self.address, self.port), timeout=5)
                connection.settimeout(0.25)
                with self._socket_lock:
                    if self._stop.is_set():
                        connection.close()
                        return
                    self._socket = connection

                if self._has_joined and isinstance(self._room_code, str):
                    entry_type = "join_room"
                    entry_payload = {
                        "username": self._username,
                        "room_code": self._room_code,
                    }
                    if isinstance(self._resume_token, str):
                        entry_payload["resume_token"] = self._resume_token
                else:
                    entry_type = self.entry_message_type
                    entry_payload = self.entry_payload
                if not self.send_message(entry_type, entry_payload, uuid.uuid4().hex):
                    raise ConnectionError("could not send room entry request")

                message_buffer = MessageBuffer()
                last_pong = time.monotonic()
                next_ping = last_pong + self.heartbeat_interval
                while not self._stop.is_set():
                    now = time.monotonic()
                    if now - last_pong >= self.heartbeat_timeout:
                        raise TimeoutError("server heartbeat timed out")
                    if now >= next_ping:
                        if not self.send_message("ping", {}, uuid.uuid4().hex):
                            raise ConnectionError("could not send heartbeat")
                        next_ping = now + self.heartbeat_interval
                    try:
                        chunk = connection.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise ConnectionError("server closed the connection")
                    for message in message_buffer.feed(chunk):
                        message_type = message.get("type")
                        payload = message.get("payload")
                        if message_type == "pong":
                            last_pong = time.monotonic()
                        elif message_type in {"room_created", "room_joined"} and isinstance(payload, dict):
                            self._room_code = payload.get("room_code")
                            self._username = payload.get("username", self._username)
                            self._resume_token = payload.get("resume_token", self._resume_token)
                            self._has_joined = isinstance(self._room_code, str)
                            retry_index = 0
                            reported_disconnect = False
                        self.message_received.emit(message)
            except (OSError, ProtocolError, TimeoutError) as exc:
                last_reason = str(exc) or "connection lost"
            finally:
                with self._socket_lock:
                    if self._socket is connection:
                        self._socket = None
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass

            if self._stop.is_set():
                return
            if not reported_disconnect:
                self.disconnected.emit(last_reason)
                reported_disconnect = True
            if not self._has_joined and retry_index >= len(self.reconnect_delays):
                self.disconnected.emit(last_reason)
                return
            delay = self.reconnect_delays[min(retry_index, len(self.reconnect_delays) - 1)]
            retry_index += 1
            self.reconnecting.emit(f"Reconnecting in {delay:g}s · {last_reason}")
            if self._stop.wait(delay):
                return
