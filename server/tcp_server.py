"""Threaded TCP server using the shared newline-delimited JSON protocol."""

import logging
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from server.persistence import PersistenceManager
from server.session_manager import RoomChange, SessionError, SessionManager
from shared.protocol import MessageBuffer, ProtocolError, encode_message


logger = logging.getLogger(__name__)


@dataclass
class ClientConnection:
    socket: socket.socket
    address: tuple[str, int]
    send_lock: threading.Lock = field(default_factory=threading.Lock)


class TCPServer:
    """Accept concurrent clients and route validated room messages."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5000,
        boards_directory: str | Path | None = None,
        client_timeout: float = 45.0,
        reconnect_grace_period: float = 30.0,
        max_clients: int = 128,
    ) -> None:
        self.host = host
        self.port = port
        self.client_timeout = max(0.5, client_timeout)
        self.socket_poll_interval = min(0.5, self.client_timeout)
        self.max_clients = max(1, max_clients)
        self.server_address: tuple[str, int] | None = None
        self._listener: socket.socket | None = None
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._room_events_lock = threading.Lock()
        self._room_locks_guard = threading.Lock()
        self._room_locks: dict[str, threading.Lock] = {}
        self._clients: dict[str, ClientConnection] = {}
        self._handlers: set[threading.Thread] = set()
        self.persistence = PersistenceManager(boards_directory)
        self.sessions = SessionManager(self.persistence, reconnect_grace_period)

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
            logger.info("TCP server listening on %s:%s", *self.server_address)

            while not self._stopping.is_set():
                self.sessions.prune_expired_rooms()
                try:
                    connection, _address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        break
                    raise

                connection.settimeout(self.socket_poll_interval)
                with self._lock:
                    at_capacity = len(self._clients) >= self.max_clients
                if at_capacity:
                    logger.warning("rejecting connection from %s: client limit reached", _address)
                    self._close_socket(connection)
                    continue

                client_id = uuid.uuid4().hex
                handler = threading.Thread(
                    target=self._handle_client,
                    args=(client_id, connection),
                    daemon=True,
                )
                with self._lock:
                    self._clients[client_id] = ClientConnection(connection, _address)
                    self._handlers.add(handler)
                handler.start()
                logger.info("client connected id=%s address=%s", client_id, _address)
        except Exception:
            logger.exception("TCP server failed")
            raise
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
            logger.info("TCP server stopped")

    def shutdown(self) -> None:
        """Request shutdown and disconnect clients so handlers can finish."""
        self._stopping.set()
        logger.info("TCP server shutdown requested")
        with self._lock:
            clients = [client.socket for client in self._clients.values()]
        for client_socket in clients:
            self._close_socket(client_socket)

    def _handle_client(self, client_id: str, connection: socket.socket) -> None:
        message_buffer = MessageBuffer()
        last_activity = time.monotonic()
        try:
            while not self._stopping.is_set():
                try:
                    data = connection.recv(4096)
                except socket.timeout:
                    if time.monotonic() - last_activity >= self.client_timeout:
                        logger.info("client timed out id=%s", client_id)
                        break
                    continue
                if not data:
                    logger.info("client disconnected id=%s", client_id)
                    break
                last_activity = time.monotonic()
                try:
                    messages = message_buffer.feed(data)
                except ProtocolError as exc:
                    logger.warning("protocol error client=%s error=%s", client_id, exc)
                    self._send_message(
                        client_id, "protocol_error", None, {"error": str(exc)}
                    )
                    break
                for message in messages:
                    self._handle_message(client_id, message)
        except OSError:
            # A disconnect or server shutdown ends this client's handler only.
            logger.info("socket closed client=%s", client_id)
        finally:
            try:
                with self._event_lock_for_client(client_id):
                    change = self.sessions.disconnect(client_id)
                    if change:
                        self._broadcast_participants(change)
            except Exception:
                logger.exception("failed to clean up client session id=%s", client_id)
            self._close_socket(connection)
            with self._lock:
                self._clients.pop(client_id, None)
                self._handlers.discard(threading.current_thread())
            logger.info("client handler finished id=%s", client_id)

    def _handle_message(self, client_id: str, message: dict) -> None:
        """Route room commands and retain the protocol's generic JSON echo."""
        message_type = message["type"]
        request_id = message["request_id"]
        payload = message["payload"]
        try:
            if message_type == "ping":
                if payload not in ({}, None):
                    raise SessionError("invalid_request", "ping payload must be empty")
                self._send_message(client_id, "pong", request_id, {})
            elif message_type == "pong":
                return
            elif message_type == "create_room":
                if not isinstance(payload, dict):
                    raise SessionError("invalid_request", "payload must be an object")
                with self._room_events_lock:
                    change = self.sessions.create_room(client_id, payload.get("username"))
                    self._send_room_confirmation(client_id, "room_created", request_id, change)
                    self._broadcast_participants(change)
            elif message_type == "join_room":
                if not isinstance(payload, dict):
                    raise SessionError("invalid_request", "payload must be an object")
                room_code = payload.get("room_code")
                lock = self._event_lock_for_room(room_code)
                with lock:
                    change = self.sessions.join_room(
                        client_id,
                        payload.get("room_code"),
                        payload.get("username"),
                        payload.get("resume_token"),
                    )
                    if change.replaced_client_id:
                        logger.info(
                            "replacing stale session old=%s new=%s",
                            change.replaced_client_id,
                            client_id,
                        )
                        self._close_client(change.replaced_client_id)
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
                with self._event_lock_for_client(client_id):
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
                with self._event_lock_for_client(client_id):
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
                with self._event_lock_for_client(client_id):
                    change = self.sessions.change_drawing_status(client_id, action)
                    for recipient_id in change.recipients:
                        self._send_message(
                            recipient_id,
                            "drawing_operation_status",
                            request_id if recipient_id == client_id else None,
                            change.status,
                        )
            elif message_type == "cursor_update":
                with self._event_lock_for_client(client_id):
                    change = self.sessions.update_cursor(client_id, payload)
                    for recipient_id in change.recipients:
                        self._send_message(
                            recipient_id,
                            "cursor_update",
                            None,
                            change.cursor,
                        )
            elif message_type == "chat_message":
                with self._event_lock_for_client(client_id):
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
                with self._event_lock_for_client(client_id):
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
            logger.warning(
                "request rejected client=%s type=%s code=%s",
                client_id,
                message_type,
                exc.code,
            )
            self._send_message(
                client_id,
                "error",
                request_id,
                {"code": exc.code, "message": str(exc)},
            )
        except Exception:
            logger.exception("request failed client=%s type=%s", client_id, message_type)
            self._send_message(
                client_id,
                "error",
                request_id,
                {"code": "internal_error", "message": "server could not process the request"},
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
                "resume_token": change.resume_token,
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
            logger.info("failed to send to disconnected client id=%s", client_id)
            self._close_socket(client.socket)

    def _close_client(self, client_id: str) -> None:
        with self._lock:
            client = self._clients.get(client_id)
        if client is not None:
            self._close_socket(client.socket)

    def _event_lock_for_client(self, client_id: str) -> threading.Lock:
        room_code = self.sessions.room_code_for_client(client_id)
        if room_code is None:
            return self._room_events_lock
        return self._event_lock_for_room(room_code)

    def _event_lock_for_room(self, room_code: object) -> threading.Lock:
        if not isinstance(room_code, str):
            return self._room_events_lock
        lock_key = room_code.upper()
        with self._room_locks_guard:
            return self._room_locks.setdefault(lock_key, threading.Lock())

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
