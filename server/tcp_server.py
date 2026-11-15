"""Threaded TCP server using the shared newline-delimited JSON protocol."""

import socket
import threading
from shared.protocol import MessageBuffer, ProtocolError, encode_message


class TCPServer:
    """Accept clients and echo validated JSON messages until shutdown."""

    def __init__(self, host: str = "0.0.0.0", port: int = 5000) -> None:
        self.host = host
        self.port = port
        self.server_address: tuple[str, int] | None = None
        self._listener: socket.socket | None = None
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._clients: set[socket.socket] = set()
        self._handlers: set[threading.Thread] = set()

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

                handler = threading.Thread(
                    target=self._handle_client,
                    args=(connection,),
                    daemon=True,
                )
                with self._lock:
                    self._clients.add(connection)
                    self._handlers.add(handler)
                handler.start()
        finally:
            self._ready.set()
            listener.close()
            with self._lock:
                clients = list(self._clients)
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
            clients = list(self._clients)
        for connection in clients:
            self._close_socket(connection)

    def _handle_client(self, connection: socket.socket) -> None:
        message_buffer = MessageBuffer()
        try:
            while not self._stopping.is_set():
                data = connection.recv(4096)
                if not data:
                    break
                try:
                    messages = message_buffer.feed(data)
                except ProtocolError as exc:
                    self._send_protocol_error(connection, str(exc))
                    break
                for message in messages:
                    connection.sendall(
                        encode_message(
                            message["type"], message["request_id"], message["payload"]
                        )
                    )
        except OSError:
            # A disconnect or server shutdown ends this client's handler only.
            pass
        finally:
            self._close_socket(connection)
            with self._lock:
                self._clients.discard(connection)
                self._handlers.discard(threading.current_thread())

    @staticmethod
    def _send_protocol_error(connection: socket.socket, detail: str) -> None:
        """Report a bad message when possible, then let its handler close it."""
        try:
            connection.sendall(
                encode_message("protocol_error", None, {"error": detail})
            )
        except (OSError, ProtocolError):
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
