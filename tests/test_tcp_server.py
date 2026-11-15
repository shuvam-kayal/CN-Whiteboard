import socket
import threading

import pytest

from server.tcp_server import TCPServer
from shared.protocol import MessageBuffer, encode_message


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
        client.sendall(encode_message("echo", "test-1", {"text": "hello"}))
        assert MessageBuffer().feed(client.recv(1024)) == [
            {"type": "echo", "request_id": "test-1", "payload": {"text": "hello"}}
        ]


def test_three_clients_exchange_data_and_disconnect_independently(running_server) -> None:
    _server, _thread, address = running_server
    clients = [socket.create_connection(address, timeout=2) for _ in range(3)]
    try:
        for index, client in enumerate(clients):
            client.sendall(encode_message("echo", str(index), {"client": index}))
        for index, client in enumerate(clients):
            assert MessageBuffer().feed(client.recv(1024)) == [
                {"type": "echo", "request_id": str(index), "payload": {"client": index}}
            ]

        # Closing one peer must leave the others usable.
        clients[0].close()
        clients[1].sendall(encode_message("echo", "live-1", "still connected"))
        assert MessageBuffer().feed(clients[1].recv(1024))[0]["payload"] == "still connected"
        clients[2].sendall(encode_message("echo", "live-2", "also connected"))
        assert MessageBuffer().feed(clients[2].recv(1024))[0]["payload"] == "also connected"
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


def test_server_reports_invalid_message_and_remains_available(running_server) -> None:
    _server, _thread, address = running_server
    with socket.create_connection(address, timeout=2) as bad_client:
        bad_client.sendall(b"not-json\n")
        response_buffer = MessageBuffer()
        response = []
        while not response:
            response.extend(response_buffer.feed(bad_client.recv(4096)))
        assert response[0]["type"] == "protocol_error"
        assert "malformed JSON" in response[0]["payload"]["error"]

    with socket.create_connection(address, timeout=2) as good_client:
        good_client.sendall(encode_message("echo", "after-error", "still running"))
        assert MessageBuffer().feed(good_client.recv(4096))[0]["payload"] == "still running"


def test_server_processes_multiple_messages_in_one_tcp_write(running_server) -> None:
    _server, _thread, address = running_server
    with socket.create_connection(address, timeout=2) as client:
        client.sendall(
            encode_message("echo", "one", 1) + encode_message("echo", "two", 2)
        )
        response_buffer = MessageBuffer()
        responses = []
        while len(responses) < 2:
            responses.extend(response_buffer.feed(client.recv(4096)))
        assert [message["request_id"] for message in responses] == ["one", "two"]
