"""Threaded TCP server using the shared newline-delimited JSON protocol."""

import socket
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from server.persistence import PersistenceManager
from server.session_manager import RoomChange, SessionError, SessionManager
from shared.protocol import MessageBuffer, ProtocolError, encode_message


@dataclass
class ClientConnection:
    socket: socket.socket
    send_lock: threading.Lock = field(default_factory=threading.Lock)


class TCPServer:
    """Accept clients and echo validated JSON messages until shutdown."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5000,
        boards_directory: str | Path | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.server_address: tuple[str, int] | None = None
        self._listener: socket.socket | None = None
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._room_events_lock = threading.Lock()
        self._clients: dict[str, ClientConnection] = {}
        self._handlers: set[threading.Thread] = set()
        self.persistence = PersistenceManager(boards_directory)
        self.sessions = SessionManager(self.persistence)

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Wait until the listening socket has been bound."""
        return self._ready.wait(timeout)

    def serve_forever(self) -> None:
        """Bind and accept clients until :meth:`shutdown` is called."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self.host, self.port))
            listener.listen()
            listener.settimeout(0.2)
            self._listener = listener
            self.server_address = listener.getsockname()
            self._ready.set()

            while not self._stopping.is_set():
                try:
                    connection, _address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        break
                    raise

                client_id = uuid.uuid4().hex
                handler = threading.Thread(
                    target=self._handle_client,
                    args=(client_id, connection),
                    daemon=True,
                )
                with self._lock:
                    self._clients[client_id] = ClientConnection(connection)
                    self._handlers.add(handler)
                handler.start()
        finally:
            self._ready.set()
            listener.close()
            with self._lock:
                clients = [client.socket for client in self._clients.values()]
                handlers = list(self._handlers)
            for connection in clients:
                self._close_socket(connection)
            for handler in handlers:
                if handler is not threading.current_thread():
                    handler.join()
            self._listener = None

    def shutdown(self) -> None:
        """Request shutdown and disconnect clients so handlers can finish."""
        self._stopping.set()
        with self._lock:
            clients = [client.socket for client in self._clients.values()]
        for client_socket in clients:
            self._close_socket(client_socket)

    def _handle_client(self, client_id: str, connection: socket.socket) -> None:
        message_buffer = MessageBuffer()
        try:
            while not self._stopping.is_set():
                data = connection.recv(4096)
                if not data:
                    break
                try:
                    messages = message_buffer.feed(data)
                except ProtocolError as exc:
                    self._send_message(
                        client_id, "protocol_error", None, {"error": str(exc)}
                    )
                    break
                for message in messages:
                    self._handle_message(client_id, message)
        except OSError:
            # A disconnect or server shutdown ends this client's handler only.
            pass
        finally:
            with self._room_events_lock:
                change = self.sessions.disconnect(client_id)
                if change:
                    self._broadcast_participants(change)
            self._close_socket(connection)
            with self._lock:
                self._clients.pop(client_id, None)
                self._handlers.discard(threading.current_thread())

    def _handle_message(self, client_id: str, message: dict) -> None:
        """Route room commands and retain the protocol's generic JSON echo."""
        message_type = message["type"]
        request_id = message["request_id"]
        payload = message["payload"]
        try:
            if message_type == "create_room":
                if not isinstance(payload, dict):
                    raise SessionError("invalid_request", "payload must be an object")
                with self._room_events_lock:
                    change = self.sessions.create_room(client_id, payload.get("username"))
                    self._send_room_confirmation(client_id, "room_created", request_id, change)
                    self._broadcast_participants(change)
            elif message_type == "join_room":
                if not isinstance(payload, dict):
                    raise SessionError("invalid_request", "payload must be an object")
                with self._room_events_lock:
                    change = self.sessions.join_room(
                        client_id, payload.get("room_code"), payload.get("username")
                    )
                    self._send_room_confirmation(client_id, "room_joined", request_id, change)
                    self._broadcast_participants(change)
                    self._send_message(
                        client_id,
                        "board_state",
                        None,
                        {
                            "room_code": change.room_code,
                            "operations": self.sessions.get_drawing_operations(change.room_code),
                        },
                    )
            elif message_type == "leave_room":
                with self._room_events_lock:
                    change = self.sessions.leave_room(client_id)
                    if change is None:
                        raise SessionError("not_in_room", "client is not in a room")
                    self._send_message(
                        client_id,
                        "room_left",
                        request_id,
                        {"room_code": change.room_code},
                    )
                    self._broadcast_participants(change)
            elif message_type == "drawing_operation":
                with self._room_events_lock:
                    change = self.sessions.add_drawing_operation(client_id, payload)
                    self._send_message(
                        client_id,
                        "drawing_accepted",
                        request_id,
                        {
                            "room_code": change.operation["room_code"],
                            "sequence_number": change.operation["sequence_number"],
                        },
                    )
                    for recipient_id in change.recipients:
                        self._send_message(
                            recipient_id,
                            "drawing_operation",
                            None,
                            change.operation,
                        )
            elif message_type in {"undo_operation", "redo_operation"}:
                if payload not in ({}, None):
                    raise SessionError("invalid_request", "undo/redo payload must be empty")
                action = "undo" if message_type == "undo_operation" else "redo"
                with self._room_events_lock:
                    change = self.sessions.change_drawing_status(client_id, action)
                    for recipient_id in change.recipients:
                        self._send_message(
                            recipient_id,
                            "drawing_operation_status",
                            request_id if recipient_id == client_id else None,
                            change.status,
                        )
            elif message_type == "cursor_update":
                with self._room_events_lock:
                    change = self.sessions.update_cursor(client_id, payload)
                    for recipient_id in change.recipients:
                        self._send_message(
                            recipient_id,
                            "cursor_update",
                            None,
                            change.cursor,
                        )
            elif message_type == "chat_message":
                with self._room_events_lock:
                    change = self.sessions.create_chat_message(client_id, payload)
                    for recipient_id in change.recipients:
                        self._send_message(
                            recipient_id,
                            "chat_message",
                            None,
                            change.message,
                        )
            elif message_type in {"save_board", "load_board"}:
                if payload not in ({}, None):
                    raise SessionError("invalid_request", "board request payload must be empty")
                with self._room_events_lock:
                    if message_type == "save_board":
                        result = self.sessions.save_board(client_id)
                        self._send_message(client_id, "board_saved", request_id, result)
                    else:
                        change = self.sessions.load_board(client_id)
                        self._send_message(
                            client_id,
                            "board_loaded",
                            request_id,
                            {"room_code": change.room_code, "saved_at": change.saved_at,
                             "operation_count": len(change.operations)},
                        )
                        for recipient_id in change.recipients:
                            self._send_message(
                                recipient_id,
                                "board_state",
                                None,
                                {"room_code": change.room_code, "operations": change.operations},
                            )
            else:
                self._send_message(client_id, message_type, request_id, payload)
        except SessionError as exc:
            self._send_message(
                client_id,
                "error",
                request_id,
                {"code": exc.code, "message": str(exc)},
            )

    def _send_room_confirmation(
        self,
        client_id: str,
        message_type: str,
        request_id: str | None,
        change: RoomChange,
    ) -> None:
        participant = change.participant or {}
        self._send_message(
            client_id,
            message_type,
            request_id,
            {
                "room_code": change.room_code,
                "participant_id": participant.get("participant_id"),
                "username": participant.get("username"),
                "is_host": participant.get("is_host", False),
            },
        )

    def _broadcast_participants(self, change: RoomChange) -> None:
        payload = {"room_code": change.room_code, "participants": change.participants}
        for client_id in change.recipients:
            self._send_message(client_id, "participants_updated", None, payload)

    def _send_message(
        self,
        client_id: str,
        message_type: str,
        request_id: str | None,
        payload: object,
    ) -> None:
        with self._lock:
            client = self._clients.get(client_id)
        if client is None:
            return
        try:
            encoded = encode_message(message_type, request_id, payload)
            with client.send_lock:
                client.socket.sendall(encoded)
        except (OSError, ProtocolError):
            # A vanished peer should not interrupt room updates for other clients.
            pass

    @staticmethod
    def _close_socket(connection: socket.socket) -> None:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            connection.close()
        except OSError:
            pass
