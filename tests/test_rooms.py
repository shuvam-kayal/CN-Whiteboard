import socket
import threading

import pytest

from server.tcp_server import TCPServer
from shared.protocol import MessageBuffer, encode_message


class JsonClient:
    def __init__(self, address: tuple[str, int]) -> None:
        self.socket = socket.create_connection(address, timeout=3)
        self.buffer = MessageBuffer()
        self.pending: list[dict] = []

    def send(self, message_type: str, request_id: str, payload: dict) -> None:
        self.socket.sendall(encode_message(message_type, request_id, payload))

    def receive(self) -> dict:
        while not self.pending:
            data = self.socket.recv(4096)
            if not data:
                raise AssertionError("server disconnected before sending a response")
            self.pending.extend(self.buffer.feed(data))
        return self.pending.pop(0)

    def close(self) -> None:
        self.socket.close()


@pytest.fixture
def room_server():
    server = TCPServer(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3), "server did not start listening"
    assert server.server_address is not None
    yield server, server.server_address
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive(), "server did not shut down cleanly"


def create_room(client: JsonClient, username: str = "Host") -> tuple[str, dict]:
    client.send("create_room", "create", {"username": username})
    created = client.receive()
    participants_update = client.receive()
    assert created["type"] == "room_created"
    assert participants_update["type"] == "participants_updated"
    return created["payload"]["room_code"], created["payload"]


def join_room(client: JsonClient, code: str, username: str) -> tuple[dict, dict]:
    client.send("join_room", "join", {"room_code": code, "username": username})
    joined = client.receive()
    participants_update = client.receive()
    return joined, participants_update


def test_socket_room_creation_and_joining(room_server) -> None:
    _server, address = room_server
    host = JsonClient(address)
    guest = JsonClient(address)
    try:
        code, host_info = create_room(host, "Mira")
        assert len(code) == 6
        assert host_info["username"] == "Mira"
        assert host_info["is_host"] is True
        assert host_info["participant_id"]

        joined, update = join_room(guest, code.lower(), "Lee")
        assert joined["type"] == "room_joined"
        assert joined["payload"]["room_code"] == code
        assert joined["payload"]["is_host"] is False
        assert joined["payload"]["participant_id"] != host_info["participant_id"]
        assert [p["username"] for p in update["payload"]["participants"]] == ["Mira", "Lee"]

        host_update = host.receive()
        assert host_update["payload"]["participants"] == update["payload"]["participants"]
    finally:
        host.close()
        guest.close()


def test_multiple_participants_get_unique_names_and_ids(room_server) -> None:
    _server, address = room_server
    clients = [JsonClient(address) for _ in range(3)]
    try:
        code, first = create_room(clients[0], "Alex")
        joined_two, update_two = join_room(clients[1], code, "Alex")
        clients[0].receive()
        joined_three, update_three = join_room(clients[2], code, "ALEX")
        clients[0].receive()
        clients[1].receive()

        people = update_three["payload"]["participants"]
        assert [p["username"] for p in people] == ["Alex", "Alex (2)", "ALEX (3)"]
        ids = {p["participant_id"] for p in people}
        assert len(ids) == 3
        assert first["participant_id"] in ids
        assert joined_two["payload"]["participant_id"] in ids
        assert joined_three["payload"]["participant_id"] in ids
        assert len(update_two["payload"]["participants"]) == 2
    finally:
        for client in clients:
            client.close()


def test_rooms_are_isolated(room_server) -> None:
    _server, address = room_server
    host_one, host_two, guest = [JsonClient(address) for _ in range(3)]
    try:
        code_one, _ = create_room(host_one, "One")
        code_two, _ = create_room(host_two, "Two")
        assert code_one != code_two

        joined, update = join_room(guest, code_one, "Guest")
        assert joined["payload"]["room_code"] == code_one
        assert update["payload"]["room_code"] == code_one
        assert len(update["payload"]["participants"]) == 2
        assert host_one.receive()["payload"]["room_code"] == code_one

        host_two.socket.settimeout(0.2)
        with pytest.raises(socket.timeout):
            host_two.receive()
    finally:
        host_one.close()
        host_two.close()
        guest.close()


def test_invalid_and_missing_room_codes_are_rejected(room_server) -> None:
    _server, address = room_server
    client = JsonClient(address)
    try:
        client.send("join_room", "bad-format", {"room_code": "bad!", "username": "Sam"})
        error = client.receive()
        assert error["type"] == "error"
        assert error["payload"]["code"] == "invalid_room_code"

        client.send("join_room", "missing", {"room_code": "ABC234", "username": "Sam"})
        error = client.receive()
        assert error["payload"]["code"] == "room_not_found"
    finally:
        client.close()


def test_disconnect_broadcasts_and_removes_empty_room(room_server) -> None:
    server, address = room_server
    host, guest = JsonClient(address), JsonClient(address)
    code, _ = create_room(host, "Host")
    join_room(guest, code, "Guest")
    host.receive()

    guest.close()
    update = host.receive()
    assert update["type"] == "participants_updated"
    assert [p["username"] for p in update["payload"]["participants"]] == ["Host"]

    host.close()
    for _ in range(30):
        if server.sessions.room_count == 0:
            break
        threading.Event().wait(0.02)
    assert server.sessions.room_count == 0


def test_host_disconnect_promotes_next_participant(room_server) -> None:
    _server, address = room_server
    host, guest = JsonClient(address), JsonClient(address)
    try:
        code, _ = create_room(host, "Host")
        join_room(guest, code, "Guest")
        host.receive()
        host.close()
        update = guest.receive()
        participants = update["payload"]["participants"]
        assert len(participants) == 1
        assert participants[0]["username"] == "Guest"
        assert participants[0]["is_host"] is True
    finally:
        guest.close()


def test_explicit_leave_broadcasts_and_allows_a_new_room(room_server) -> None:
    _server, address = room_server
    host, guest = JsonClient(address), JsonClient(address)
    try:
        code, _ = create_room(host, "Host")
        join_room(guest, code, "Guest")
        host.receive()

        guest.send("leave_room", "leave", {})
        left = guest.receive()
        assert left["type"] == "room_left"
        update = host.receive()
        assert [p["username"] for p in update["payload"]["participants"]] == ["Host"]

        new_code, details = create_room(guest, "Guest")
        assert new_code != code
        assert details["is_host"] is True
    finally:
        host.close()
        guest.close()
