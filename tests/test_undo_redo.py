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

    def send(self, kind: str, request_id: str | None, payload: dict | None = None) -> None:
        self.socket.sendall(encode_message(kind, request_id, payload or {}))

    def receive(self) -> dict:
        while not self.pending:
            data = self.socket.recv(4096)
            if not data:
                raise AssertionError("server disconnected before response")
            self.pending.extend(self.buffer.feed(data))
        return self.pending.pop(0)

    def close(self) -> None:
        self.socket.close()


@pytest.fixture
def undo_server(tmp_path):
    server = TCPServer("127.0.0.1", 0, boards_directory=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3)
    assert server.server_address is not None
    yield server, server.server_address
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def create_room(peer: Peer) -> str:
    peer.send("create_room", "create", {"username": "Alice"})
    room = peer.receive()
    peer.receive()
    return room["payload"]["room_code"]


def join_room(peer: Peer, code: str, username: str) -> None:
    peer.send("join_room", "join", {"room_code": code, "username": username})
    peer.receive()
    peer.receive()
    peer.receive()  # current board snapshot


def draw(peer: Peer, request_id: str, *, tool: str = "pen", x: int = 1) -> int:
    peer.send(
        "drawing_operation",
        request_id,
        {
            "operation_type": tool,
            "points": [[x, x + 1]],
            "color": "#ffffff" if tool == "eraser" else "#112233",
            "brush_size": 7,
        },
    )
    return peer.receive()["payload"]["sequence_number"]


def assert_status(peer: Peer, sequence: int, active: bool, action: str) -> None:
    message = peer.receive()
    assert message["type"] == "drawing_operation_status"
    assert message["payload"]["sequence_number"] == sequence
    assert message["payload"]["active"] is active
    assert message["payload"]["action"] == action


def test_participant_can_only_undo_and_redo_their_own_operations(undo_server) -> None:
    _server, address = undo_server
    alice, bob, charlie = [Peer(address) for _ in range(3)]
    try:
        room = create_room(alice)
        join_room(bob, room, "Bob")
        alice.receive()  # participant update
        join_room(charlie, room, "Charlie")
        alice.receive()
        bob.receive()

        assert draw(alice, "alice-stroke") == 1
        bob.receive()  # drawing_operation
        charlie.receive()  # drawing_operation
        assert draw(bob, "bob-stroke", tool="eraser", x=10) == 2
        alice.receive()  # drawing_operation
        charlie.receive()  # drawing_operation

        charlie.send("undo_operation", "empty-undo")
        empty_undo = charlie.receive()
        assert empty_undo["type"] == "error", empty_undo
        assert empty_undo["payload"]["code"] == "nothing_to_undo", empty_undo

        bob.send("undo_operation", "bob-undo")
        assert_status(bob, 2, False, "undo")
        assert_status(alice, 2, False, "undo")
        assert [op["active"] for op in _server.sessions.get_drawing_operations(room)] == [True, False]

        bob.send("redo_operation", "bob-redo")
        assert_status(bob, 2, True, "redo")
        assert_status(alice, 2, True, "redo")

        alice.send("undo_operation", "alice-undo")
        assert_status(alice, 1, False, "undo")
        assert_status(bob, 1, False, "undo")
        assert [op["active"] for op in _server.sessions.get_drawing_operations(room)] == [False, True]
    finally:
        for peer in (alice, bob, charlie):
            peer.close()


def test_multiple_undo_redo_operations_restore_original_order(undo_server) -> None:
    _server, address = undo_server
    alice, bob = Peer(address), Peer(address)
    try:
        room = create_room(alice)
        join_room(bob, room, "Bob")
        alice.receive()

        draw(alice, "first", x=1)
        assert bob.receive()["type"] == "drawing_operation"
        draw(alice, "second", tool="eraser", x=2)
        assert bob.receive()["type"] == "drawing_operation"

        for request, expected in (("undo-second", 2), ("undo-first", 1)):
            alice.send("undo_operation", request)
            assert_status(alice, expected, False, "undo")
            assert_status(bob, expected, False, "undo")
        assert [op["active"] for op in _server.sessions.get_drawing_operations(room)] == [False, False]

        for request, expected in (("redo-first", 1), ("redo-second", 2)):
            alice.send("redo_operation", request)
            assert_status(alice, expected, True, "redo")
            assert_status(bob, expected, True, "redo")
        operations = _server.sessions.get_drawing_operations(room)
        assert [op["sequence_number"] for op in operations] == [1, 2]
        assert [op["active"] for op in operations] == [True, True]

        alice.send("undo_operation", "empty-redo-reset")
        assert_status(alice, 2, False, "undo")
        assert_status(bob, 2, False, "undo")
        draw(alice, "new-operation", x=3)  # a new stroke clears the redo stack
        assert bob.receive()["type"] == "drawing_operation"
        alice.send("redo_operation", "cleared-redo")
        assert alice.receive()["payload"]["code"] == "nothing_to_redo"
    finally:
        alice.close()
        bob.close()


def test_undo_redo_state_survives_save_load_and_server_restart(tmp_path) -> None:
    server = TCPServer("127.0.0.1", 0, boards_directory=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3)
    assert server.server_address is not None
    alice = Peer(server.server_address)
    room = create_room(alice)
    draw(alice, "stroke")
    alice.send("undo_operation", "undo")
    assert_status(alice, 1, False, "undo")
    alice.send("save_board", "save")
    assert alice.receive()["type"] == "board_saved"
    alice.close()
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()

    restored_server = TCPServer("127.0.0.1", 0, boards_directory=tmp_path)
    restored_thread = threading.Thread(target=restored_server.serve_forever, daemon=True)
    restored_thread.start()
    assert restored_server.wait_until_ready(timeout=3)
    assert restored_server.server_address is not None
    resumed = Peer(restored_server.server_address)
    try:
        resumed.send("join_room", "resume", {"room_code": room, "username": "Alice"})
        assert resumed.receive()["payload"]["is_host"] is True
        resumed.receive()  # participants_updated
        snapshot = resumed.receive()
        operation = snapshot["payload"]["operations"][0]
        assert operation["active"] is False
        assert restored_server.sessions._rooms[room].redo_stacks

        # A room load restores both the inactive flag and the redo stack metadata.
        resumed.send("load_board", "reload")
        assert resumed.receive()["type"] == "board_loaded"
        assert resumed.receive()["type"] == "board_state"
        resumed.send("redo_operation", "redo")
        assert_status(resumed, 1, True, "redo")
        assert restored_server.sessions.get_drawing_operations(room)[0]["active"] is True
    finally:
        resumed.close()
        restored_server.shutdown()
        restored_thread.join(timeout=3)
        assert not restored_thread.is_alive()
