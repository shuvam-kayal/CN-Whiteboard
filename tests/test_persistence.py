import json
import socket
import threading

import pytest

from server.persistence import PersistenceError, PersistenceManager
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
            data = self.socket.recv(4096)
            if not data:
                raise AssertionError("server disconnected before response")
            self.pending.extend(self.buffer.feed(data))
        return self.pending.pop(0)

    def close(self) -> None:
        self.socket.close()


class RunningServer:
    def __init__(self, boards_directory) -> None:
        self.server = TCPServer("127.0.0.1", 0, boards_directory=boards_directory)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        assert self.server.wait_until_ready(timeout=3)
        self.address = self.server.server_address
        assert self.address is not None

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=3)
        assert not self.thread.is_alive()


def create_room(peer: Peer) -> str:
    peer.send("create_room", "create", {"username": "Host"})
    created = peer.receive()
    assert created["type"] == "room_created"
    peer.receive()  # participants_updated
    return created["payload"]["room_code"]


def join_room(peer: Peer, code: str, username: str) -> tuple[dict, dict]:
    peer.send("join_room", "join", {"room_code": code, "username": username})
    joined = peer.receive()
    assert joined["type"] == "room_joined"
    peer.receive()  # participants_updated
    snapshot = peer.receive()
    assert snapshot["type"] == "board_state"
    return joined, snapshot


def add_stroke(peer: Peer, request_id: str, x: int) -> dict:
    peer.send(
        "drawing_operation",
        request_id,
        {"operation_type": "pen", "points": [[x, x + 1]], "color": "#123456", "brush_size": 4},
    )
    response = peer.receive()
    assert response["type"] == "drawing_accepted"
    return response["payload"]


def test_persistence_manager_saves_validated_state_atomically(tmp_path) -> None:
    manager = PersistenceManager(tmp_path)
    operation = {
        "operation_type": "pen", "points": [[1, 2]], "color": "#123456",
        "brush_size": 4, "sequence_number": 1, "participant_id": "member",
        "room_code": "ABCD23", "active": True,
    }
    saved = manager.save_board("ABCD23", [operation])
    loaded = manager.load_board("ABCD23")
    normalized_operation = {**operation, "brush_type": "pen", "opacity": 1.0}
    assert saved.operations == loaded.operations == [normalized_operation]
    assert (tmp_path / "ABCD23.json").exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_slice_nine_board_file_loads_with_active_strokes(tmp_path) -> None:
    old_operation = {
        "operation_type": "pen", "points": [[1, 2]], "color": "#123456",
        "brush_size": 4, "sequence_number": 1, "participant_id": "member",
        "room_code": "ABCD23",
    }
    (tmp_path / "ABCD23.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "room_code": "ABCD23",
                "saved_at": "2026-11-15T00:00:00Z",
                "operations": [old_operation],
            }
        ),
        encoding="utf-8",
    )
    restored = PersistenceManager(tmp_path).load_board("ABCD23")
    assert restored.operations[0]["active"] is True
    assert restored.undo_stacks == {"member": [1]}


@pytest.mark.parametrize("contents", ["{broken", "[]", json.dumps({"format_version": 99})])
def test_invalid_saved_json_is_rejected(tmp_path, contents) -> None:
    (tmp_path / "ABCD23.json").write_text(contents, encoding="utf-8")
    with pytest.raises(PersistenceError):
        PersistenceManager(tmp_path).load_board("ABCD23")


def test_saved_operations_are_validated_and_room_code_is_safe(tmp_path) -> None:
    manager = PersistenceManager(tmp_path)
    with pytest.raises(PersistenceError):
        manager.save_board("../../evil", [])
    with pytest.raises(PersistenceError):
        manager.save_board("ABCD23", [{"operation_type": "script"}])

    (tmp_path / "ABCD23.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "room_code": "ABCD23",
                "saved_at": "2026-11-15T00:00:00Z",
                "operations": [{"operation_type": "script"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PersistenceError):
        manager.load_board("ABCD23")


def test_host_only_save_load_and_load_broadcasts_snapshot(tmp_path) -> None:
    running = RunningServer(tmp_path)
    host, guest = Peer(running.address), Peer(running.address)
    try:
        code = create_room(host)
        join_room(guest, code, "Guest")
        host.receive()  # roster update after guest joined

        add_stroke(host, "stroke-1", 1)
        assert guest.receive()["type"] == "drawing_operation"

        guest.send("save_board", "guest-save", {})
        assert guest.receive()["payload"]["code"] == "host_only"
        guest.send("load_board", "guest-load", {})
        assert guest.receive()["payload"]["code"] == "host_only"

        host.send("save_board", "save", {})
        saved = host.receive()
        assert saved["type"] == "board_saved"
        assert saved["payload"]["operation_count"] == 1
        add_stroke(host, "stroke-2", 5)
        assert guest.receive()["type"] == "drawing_operation"

        host.send("load_board", "load", {})
        loaded = host.receive()
        host_state = host.receive()
        guest_state = guest.receive()
        assert loaded["type"] == "board_loaded"
        assert host_state["type"] == guest_state["type"] == "board_state"
        assert host_state["payload"] == guest_state["payload"]
        assert [item["sequence_number"] for item in host_state["payload"]["operations"]] == [1]
    finally:
        host.close()
        guest.close()
        running.close()


def test_room_state_recovers_after_server_restart_and_is_sent_on_join(tmp_path) -> None:
    first = RunningServer(tmp_path)
    host = Peer(first.address)
    code = create_room(host)
    add_stroke(host, "stroke", 8)
    save = host
    save.send("save_board", "save", {})
    assert save.receive()["type"] == "board_saved"
    host.close()
    first.close()

    second = RunningServer(tmp_path)
    resumed = Peer(second.address)
    try:
        joined, snapshot = join_room(resumed, code, "Returning host")
        assert joined["payload"]["is_host"] is True
        assert snapshot["payload"]["room_code"] == code
        assert [op["sequence_number"] for op in snapshot["payload"]["operations"]] == [1]
        assert second.server.sessions.get_drawing_operations(code)[0]["points"] == [[8, 9]]
    finally:
        resumed.close()
        second.close()


def test_corrupted_recovery_file_returns_error_without_closing_connection(tmp_path) -> None:
    (tmp_path / "ABCD23.json").write_text("not json", encoding="utf-8")
    running = RunningServer(tmp_path)
    peer = Peer(running.address)
    try:
        peer.send("join_room", "join", {"room_code": "ABCD23", "username": "Try"})
        error = peer.receive()
        assert error["type"] == "error"
        assert error["payload"]["code"] == "board_restore_failed"
        create_room(peer)
    finally:
        peer.close()
        running.close()
