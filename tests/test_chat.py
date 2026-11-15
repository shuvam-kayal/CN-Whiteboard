from datetime import datetime
import socket
import threading

import pytest

from server.tcp_server import TCPServer
from shared.protocol import MessageBuffer, encode_message


class ChatPeer:
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
def chat_server():
    server = TCPServer(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3)
    assert server.server_address is not None
    yield server, server.server_address
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def create_room(peer: ChatPeer, username: str) -> tuple[str, str]:
    peer.send("create_room", "create", {"username": username})
    confirmation = peer.receive()
    peer.receive()  # initial participant list
    return confirmation["payload"]["room_code"], confirmation["payload"]["participant_id"]


def join_room(peer: ChatPeer, code: str, username: str) -> None:
    peer.send("join_room", "join", {"username": username, "room_code": code})
    peer.receive()  # room_joined
    peer.receive()  # participants_updated


def test_chat_is_broadcast_to_every_member_in_message_order_and_isolated(
    chat_server,
) -> None:
    _server, address = chat_server
    alice, bob, charlie, dave = [ChatPeer(address) for _ in range(4)]
    try:
        room_code, alice_id = create_room(alice, "Alice")
        join_room(bob, room_code, "Bob")
        alice.receive()
        join_room(charlie, room_code, "Charlie")
        alice.receive()
        bob.receive()
        other_room, _ = create_room(dave, "Dave")
        assert other_room != room_code

        alice.send("chat_message", "chat-1", {"text": "Hello, everyone"})
        alice.send("chat_message", "chat-2", {"text": "Second message"})
        for peer in (alice, bob, charlie):
            first = peer.receive()
            second = peer.receive()
            assert first["type"] == second["type"] == "chat_message"
            assert first["payload"]["text"] == "Hello, everyone"
            assert second["payload"]["text"] == "Second message"
            assert first["payload"]["username"] == "Alice"
            assert first["payload"]["sender_id"] == alice_id
            assert first["payload"]["room_code"] == room_code
            parsed_time = datetime.fromisoformat(
                first["payload"]["timestamp"].replace("Z", "+00:00")
            )
            assert parsed_time.utcoffset().total_seconds() == 0

        dave.socket.settimeout(0.15)
        with pytest.raises(socket.timeout):
            dave.receive()
    finally:
        for peer in (alice, bob, charlie, dave):
            peer.close()


def test_empty_oversized_and_untrusted_chat_messages_are_rejected(chat_server) -> None:
    _server, address = chat_server
    alice, bob, unattached = [ChatPeer(address) for _ in range(3)]
    try:
        room_code, _ = create_room(alice, "Alice")
        join_room(bob, room_code, "Bob")
        alice.receive()

        invalid_payloads = [
            {"text": "   "},
            {"text": "x" * 2001},
            {"text": "hello", "username": "Pretending"},
            {"text": 42},
        ]
        expected_codes = [
            "empty_message",
            "message_too_long",
            "invalid_message",
            "invalid_message",
        ]
        for index, (payload, expected_code) in enumerate(
            zip(invalid_payloads, expected_codes), start=1
        ):
            alice.send("chat_message", f"invalid-{index}", payload)
            error = alice.receive()
            assert error["type"] == "error"
            assert error["payload"]["code"] == expected_code

        bob.socket.settimeout(0.15)
        with pytest.raises(socket.timeout):
            bob.receive()

        unattached.send("chat_message", "no-room", {"text": "Hello"})
        error = unattached.receive()
        assert error["type"] == "error"
        assert error["payload"]["code"] == "not_in_room"

        alice.send("chat_message", "valid", {"text": "Still connected"})
        assert alice.receive()["payload"]["text"] == "Still connected"
        assert bob.receive()["payload"]["text"] == "Still connected"
    finally:
        for peer in (alice, bob, unattached):
            peer.close()


def test_disconnected_participant_does_not_block_remaining_room_chat(chat_server) -> None:
    _server, address = chat_server
    alice, bob, charlie = [ChatPeer(address) for _ in range(3)]
    try:
        room_code, _ = create_room(alice, "Alice")
        join_room(bob, room_code, "Bob")
        alice.receive()
        join_room(charlie, room_code, "Charlie")
        alice.receive()
        bob.receive()

        bob.close()
        alice_roster = alice.receive()
        charlie_roster = charlie.receive()
        assert alice_roster["type"] == charlie_roster["type"] == "participants_updated"
        assert len(alice_roster["payload"]["participants"]) == 2

        alice.send("chat_message", "after-disconnect", {"text": "Room is still active"})
        assert alice.receive()["payload"]["text"] == "Room is still active"
        assert charlie.receive()["payload"]["text"] == "Room is still active"
    finally:
        alice.close()
        bob.close()
        charlie.close()
