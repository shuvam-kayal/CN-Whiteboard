import socket
import threading

import pytest

from server.tcp_server import TCPServer
from shared.protocol import MessageBuffer, encode_message


class JsonSocketClient:
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
def drawing_server():
    server = TCPServer(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3)
    assert server.server_address is not None
    yield server, server.server_address
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def create_room(client: JsonSocketClient, username: str) -> tuple[str, str]:
    client.send("create_room", f"create-{username}", {"username": username})
    response = client.receive()
    client.receive()  # initial participants_updated
    return response["payload"]["room_code"], response["payload"]["participant_id"]


def join_room(client: JsonSocketClient, room_code: str, username: str) -> str:
    client.send(
        "join_room",
        f"join-{username}",
        {"username": username, "room_code": room_code},
    )
    response = client.receive()
    client.receive()  # participants_updated for the joining client
    return response["payload"]["participant_id"]


def test_operations_broadcast_to_room_members_with_authoritative_sequences(
    drawing_server,
) -> None:
    server, address = drawing_server
    alice, bob, charlie, dave = [JsonSocketClient(address) for _ in range(4)]
    try:
        room_code, alice_id = create_room(alice, "Alice")
        bob_id = join_room(bob, room_code, "Bob")
        alice.receive()  # updated participants after Bob joined
        join_room(charlie, room_code, "Charlie")
        alice.receive()
        bob.receive()
        other_room, _ = create_room(dave, "Dave")
        assert other_room != room_code

        first = {
            "operation_type": "pen",
            "points": [[12, 18], [36, 42]],
            "color": "#3867d6",
            "brush_size": 6,
        }
        alice.send("drawing_operation", "alice-op-1", first)
        accepted = alice.receive()
        assert accepted["type"] == "drawing_accepted"
        assert accepted["request_id"] == "alice-op-1"
        assert accepted["payload"]["sequence_number"] == 1

        bob_op = bob.receive()
        charlie_op = charlie.receive()
        for delivered in (bob_op, charlie_op):
            assert delivered["type"] == "drawing_operation"
            assert delivered["payload"]["sequence_number"] == 1
            assert delivered["payload"]["participant_id"] == alice_id
            assert delivered["payload"]["room_code"] == room_code
            assert delivered["payload"]["points"] == first["points"]

        # Alice receives only her acceptance, never a second copy to render.
        alice.socket.settimeout(0.2)
        with pytest.raises(socket.timeout):
            alice.receive()
        dave.socket.settimeout(0.2)
        with pytest.raises(socket.timeout):
            dave.receive()

        second = {
            "operation_type": "eraser",
            "points": [[70, 80]],
            "color": "#ffffff",
            "brush_size": 12,
        }
        bob.send("drawing_operation", "bob-op-1", second)
        assert bob.receive()["payload"]["sequence_number"] == 2
        from_bob_alice = alice.receive()
        from_bob_charlie = charlie.receive()
        assert from_bob_alice["payload"]["sequence_number"] == 2
        assert from_bob_charlie["payload"]["sequence_number"] == 2
        assert from_bob_alice["payload"]["participant_id"] == bob_id

        stored = server.sessions.get_drawing_operations(room_code)
        assert [operation["sequence_number"] for operation in stored] == [1, 2]
    finally:
        for client in (alice, bob, charlie, dave):
            client.close()


def test_invalid_operation_is_rejected_without_broadcast_or_sequence_gap(
    drawing_server,
) -> None:
    server, address = drawing_server
    alice, bob, outsider, unattached = [JsonSocketClient(address) for _ in range(4)]
    try:
        room_code, _ = create_room(alice, "Alice")
        join_room(bob, room_code, "Bob")
        alice.receive()
        other_code, _ = create_room(outsider, "Outside")

        invalid = {
            "operation_type": "pen",
            "points": [[10, "not-a-coordinate"]],
            "color": "not-a-color",
            "brush_size": -2,
        }
        alice.send("drawing_operation", "invalid-1", invalid)
        error = alice.receive()
        assert error["type"] == "error"
        assert error["payload"]["code"] == "invalid_drawing_operation"
        bob.socket.settimeout(0.2)
        with pytest.raises(socket.timeout):
            bob.receive()

        unattached.send(
            "drawing_operation",
            "no-room",
            {
                "operation_type": "pen",
                "points": [[1, 2]],
                "color": "#000000",
                "brush_size": 4,
            },
        )
        no_membership = unattached.receive()
        assert no_membership["payload"].get("code") == "not_in_room", no_membership
        assert server.sessions.get_drawing_operations(room_code) == []
        assert server.sessions.get_drawing_operations(other_code) == []

        valid = {
            "operation_type": "pen",
            "points": [[10, 20]],
            "color": "#202943",
            "brush_size": 4,
        }
        alice.send("drawing_operation", "valid-1", valid)
        assert alice.receive()["payload"]["sequence_number"] == 1
        assert bob.receive()["payload"]["sequence_number"] == 1
    finally:
        for client in (alice, bob, outsider, unattached):
            client.close()


def test_joining_participant_receives_existing_room_canvas(drawing_server) -> None:
    _server, address = drawing_server
    alice, bob = JsonSocketClient(address), JsonSocketClient(address)
    try:
        room_code, _ = create_room(alice, "Alice")
        stroke = {
            "operation_type": "pen",
            "points": [[5, 9], [20, 25]],
            "color": "#279777",
            "brush_size": 8,
        }
        alice.send("drawing_operation", "stroke-before-join", stroke)
        assert alice.receive()["type"] == "drawing_accepted"

        join_room(bob, room_code, "Bob")
        replayed = bob.receive()
        assert replayed["type"] == "drawing_operation"
        assert replayed["payload"]["sequence_number"] == 1
        assert replayed["payload"]["points"] == stroke["points"]
        assert replayed["payload"]["participant_id"]
        assert alice.receive()["type"] == "participants_updated"
    finally:
        alice.close()
        bob.close()
