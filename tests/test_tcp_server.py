import socket
import threading

import pytest

from server.tcp_server import TCPServer


@pytest.fixture
def running_server():
    server = TCPServer(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3), "server did not start listening"
    assert server.server_address is not None
    yield server, thread, server.server_address
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive(), "server did not shut down cleanly"


def test_client_receives_echo(running_server) -> None:
    _server, _thread, address = running_server
    with socket.create_connection(address, timeout=2) as client:
        client.sendall(b"raw tcp payload")
        assert client.recv(1024) == b"raw tcp payload"


def test_three_clients_exchange_data_and_disconnect_independently(running_server) -> None:
    _server, _thread, address = running_server
    clients = [socket.create_connection(address, timeout=2) for _ in range(3)]
    try:
        for index, client in enumerate(clients):
            client.sendall(f"client-{index}".encode())
        for index, client in enumerate(clients):
            assert client.recv(1024) == f"client-{index}".encode()

        # Closing one peer must leave the others usable.
        clients[0].close()
        clients[1].sendall(b"still connected")
        assert clients[1].recv(1024) == b"still connected"
        clients[2].sendall(b"also connected")
        assert clients[2].recv(1024) == b"also connected"
    finally:
        for client in clients[1:]:
            client.close()


def test_server_shutdown_closes_connected_client(running_server) -> None:
    server, thread, address = running_server
    client = socket.create_connection(address, timeout=2)
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert client.recv(1) == b""
    client.close()
