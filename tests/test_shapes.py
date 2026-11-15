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

    def send(self, kind: str, request_id: str | None, payload: dict) -> None:
        self.socket.sendall(encode_message(kind, request_id, payload))

    def receive(self) -> dict:
        while not self.pending:
            chunk = self.socket.recv(4096)
            if not chunk:
                raise AssertionError("server disconnected before response")
            self.pending.extend(self.buffer.feed(chunk))
        return self.pending.pop(0)

    def close(self) -> None:
        self.socket.close()


@pytest.fixture
def shape_server(tmp_path):
    server = TCPServer("127.0.0.1", 0, boards_directory=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3)
    assert server.server_address is not None
    yield server, server.server_address
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_shapes_sync_undo_redo_and_persist_through_board_load(shape_server) -> None:
    server, address = shape_server
    alice, bob = Peer(address), Peer(address)
    try:
        alice.send("create_room", "create", {"username": "Alice"})
        created = alice.receive()
        room = created["payload"]["room_code"]
        alice.receive()  # participants_updated
        bob.send("join_room", "join", {"room_code": room, "username": "Bob"})
        bob.receive()
        bob.receive()
        bob.receive()  # board_state
        alice.receive()  # participants_updated

        for sequence, shape in enumerate(("line", "rectangle", "ellipse"), start=1):
            operation = {
                "operation_type": shape,
                "points": [[10 * sequence, 20], [80, 90]],
                "color": "#3867d6",
                "brush_size": 5,
            }
            alice.send("drawing_operation", f"shape-{sequence}", operation)
            accepted = alice.receive()
            delivered = bob.receive()
            assert accepted["type"] == "drawing_accepted"
            assert accepted["payload"]["sequence_number"] == sequence
            assert delivered["type"] == "drawing_operation"
            assert delivered["payload"]["operation_type"] == shape
            assert delivered["payload"]["points"] == operation["points"]

        alice.send("undo_operation", "undo-ellipse", {})
        for peer in (alice, bob):
            status = peer.receive()
            assert status["type"] == "drawing_operation_status"
            assert status["payload"]["sequence_number"] == 3
            assert status["payload"]["active"] is False

        alice.send("save_board", "save", {})
        assert alice.receive()["type"] == "board_saved"
        alice.send("redo_operation", "redo-ellipse", {})
        for peer in (alice, bob):
            assert peer.receive()["payload"]["active"] is True

        # Replace the live state with the saved (undone) shape state.
        alice.send("load_board", "load", {})
        assert alice.receive()["type"] == "board_loaded"
        host_snapshot = alice.receive()
        guest_snapshot = bob.receive()
        assert host_snapshot["type"] == guest_snapshot["type"] == "board_state"
        assert host_snapshot["payload"] == guest_snapshot["payload"]
        operations = host_snapshot["payload"]["operations"]
        assert [operation["operation_type"] for operation in operations] == [
            "line", "rectangle", "ellipse"
        ]
        assert [operation["active"] for operation in operations] == [True, True, False]

        alice.send("redo_operation", "redo-after-load", {})
        assert alice.receive()["payload"]["active"] is True
        assert bob.receive()["payload"]["active"] is True
        assert server.sessions.get_drawing_operations(room)[2]["active"] is True
    finally:
        alice.close()
        bob.close()


def test_server_rejects_shapes_with_invalid_endpoint_count(shape_server) -> None:
    _server, address = shape_server
    alice = Peer(address)
    try:
        alice.send("create_room", "create", {"username": "Alice"})
        alice.receive()
        alice.receive()
        alice.send(
            "drawing_operation",
            "invalid",
            {"operation_type": "ellipse", "points": [[1, 2]], "color": "#000000", "brush_size": 3},
        )
        error = alice.receive()
        assert error["type"] == "error"
        assert error["payload"]["code"] == "invalid_drawing_operation"
    finally:
        alice.close()
