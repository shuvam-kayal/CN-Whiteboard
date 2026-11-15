import socket
import threading

import pytest

from server.tcp_server import TCPServer
from shared.protocol import MessageBuffer, encode_message


class Peer:
    def __init__(self, address: tuple[str, int]) -> None:
        self.socket = socket.create_connection(address, timeout=3)
        self.buffer = MessageBuffer()
        self.pending: list[dict] = []

    def send(self, message_type: str, request_id: str | None, payload: dict) -> None:
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
def live_server():
    server = TCPServer(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3)
    assert server.server_address is not None
    yield server, server.server_address
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def create_room(peer: Peer, username: str) -> tuple[str, dict]:
    peer.send("create_room", "create", {"username": username})
    confirmation = peer.receive()
    participants = peer.receive()
    assert confirmation["type"] == "room_created"
    assert participants["type"] == "participants_updated"
    return confirmation["payload"]["room_code"], participants["payload"]["participants"][0]


def join_room(peer: Peer, room_code: str, username: str) -> list[dict]:
    peer.send("join_room", "join", {"username": username, "room_code": room_code})
    assert peer.receive()["type"] == "room_joined"
    return peer.receive()["payload"]["participants"]


def test_participant_lists_update_on_join_leave_and_disconnect(live_server) -> None:
    server, address = live_server
    alice, bob = Peer(address), Peer(address)
    try:
        room_code, alice_entry = create_room(alice, "Alice")
        assert alice_entry["username"] == "Alice"
        assert alice_entry["cursor_color"].startswith("#")

        roster = join_room(bob, room_code, "Bob")
        alice_roster = alice.receive()["payload"]["participants"]
        assert [person["username"] for person in roster] == ["Alice", "Bob"]
        assert roster == alice_roster
        assert len({person["cursor_color"] for person in roster}) == 2
        bob_entry = next(person for person in roster if person["username"] == "Bob")

        bob.send("leave_room", "leave", {})
        assert bob.receive()["type"] == "room_left"
        after_leave = alice.receive()["payload"]["participants"]
        assert [person["username"] for person in after_leave] == ["Alice"]

        rejoined = join_room(bob, room_code, "Bob")
        assert len(rejoined) == 2
        alice.receive()
        bob.close()
        after_disconnect = alice.receive()["payload"]["participants"]
        assert [person["username"] for person in after_disconnect] == ["Alice"]
        assert server.sessions.room_count == 1
        assert bob_entry["participant_id"] not in {
            person["participant_id"] for person in after_disconnect
        }
    finally:
        alice.close()
        bob.close()


def test_cursor_updates_broadcast_only_to_other_members_and_are_not_drawings(
    live_server,
) -> None:
    server, address = live_server
    alice, bob, charlie, dave = [Peer(address) for _ in range(4)]
    try:
        room_code, alice_entry = create_room(alice, "Alice")
        same_room_roster = join_room(bob, room_code, "Bob")
        alice.receive()
        join_room(charlie, room_code, "Charlie")
        alice.receive()
        bob.receive()
        other_room, _ = create_room(dave, "Dave")
        assert other_room != room_code

        colors = {person["username"]: person["cursor_color"] for person in same_room_roster}
        assert colors["Alice"] == alice_entry["cursor_color"]
        assert colors["Alice"] != colors["Bob"]

        alice.send("cursor_update", None, {"x": 0.25, "y": 0.75})
        bob_cursor = bob.receive()
        charlie_cursor = charlie.receive()
        for message in (bob_cursor, charlie_cursor):
            assert message["type"] == "cursor_update"
            assert message["payload"] == {
                "room_code": room_code,
                "participant_id": alice_entry["participant_id"],
                "username": "Alice",
                "color": alice_entry["cursor_color"],
                "x": 0.25,
                "y": 0.75,
            }

        alice.socket.settimeout(0.15)
        dave.socket.settimeout(0.15)
        with pytest.raises(socket.timeout):
            alice.receive()
        with pytest.raises(socket.timeout):
            dave.receive()
        assert server.sessions.get_drawing_operations(room_code) == []

        unattached = Peer(address)
        try:
            unattached.send("cursor_update", None, {"x": 0.5, "y": 0.5})
            error = unattached.receive()
            assert error["type"] == "error"
            assert error["payload"]["code"] == "not_in_room"
        finally:
            unattached.close()
    finally:
        for peer in (alice, bob, charlie, dave):
            peer.close()


@pytest.mark.parametrize(
    "position",
    [
        {"x": -0.1, "y": 0.5},
        {"x": True, "y": 0.2},
        {"x": 10**500, "y": 0.5},
    ],
)
def test_invalid_cursor_positions_are_rejected(live_server, position) -> None:
    _server, address = live_server
    alice = Peer(address)
    try:
        create_room(alice, "Alice")
        alice.send("cursor_update", "bad-cursor", position)
        response = alice.receive()
        assert response["type"] == "error"
        assert response["payload"]["code"] == "invalid_cursor"
    finally:
        alice.close()
