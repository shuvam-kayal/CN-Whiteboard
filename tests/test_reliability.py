import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from client.network_client import NetworkClient
from server.tcp_server import TCPServer
from shared.protocol import MAX_MESSAGE_SIZE, MessageBuffer, encode_message


class Peer:
    def __init__(self, address: tuple[str, int]) -> None:
        self.socket = socket.create_connection(address, timeout=3)
        self.buffer = MessageBuffer()
        self.pending: list[dict] = []

    def send(self, kind: str, request_id: str | None, payload: object = None) -> None:
        self.socket.sendall(encode_message(kind, request_id, {} if payload is None else payload))

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
def reliability_server(tmp_path):
    server = TCPServer("127.0.0.1", 0, boards_directory=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3)
    assert server.server_address is not None
    yield server, thread, server.server_address
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def enter_room(peer: Peer, code: str | None = None, name: str = "Host") -> str:
    if code is None:
        peer.send("create_room", "create", {"username": name})
        response = peer.receive()
        peer.receive()
        return response["payload"]["room_code"]
    peer.send("join_room", "join", {"room_code": code, "username": name})
    peer.receive()
    peer.receive()
    peer.receive()
    return code


def test_ping_receives_pong_and_bad_ping_is_rejected(reliability_server) -> None:
    _server, _thread, address = reliability_server
    peer = Peer(address)
    try:
        peer.send("ping", "pulse-1", {})
        response = peer.receive()
        assert response == {"type": "pong", "request_id": "pulse-1", "payload": {}}
        peer.send("ping", "bad-pulse", {"unexpected": True})
        assert peer.receive()["payload"]["code"] == "invalid_request"
        peer.send("echo", "still-open", "ok")
        assert peer.receive()["payload"] == "ok"
    finally:
        peer.close()


def test_idle_client_times_out_without_affecting_other_clients(tmp_path) -> None:
    server = TCPServer("127.0.0.1", 0, boards_directory=tmp_path, client_timeout=0.6)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(timeout=3)
    assert server.server_address is not None
    idle = socket.create_connection(server.server_address, timeout=3)
    idle.settimeout(2)
    try:
        assert idle.recv(1) == b""
        live = Peer(server.server_address)
        try:
            live.send("echo", "ok", {"alive": True})
            assert live.receive()["payload"] == {"alive": True}
        finally:
            live.close()
    finally:
        idle.close()
        server.shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_oversized_message_disconnects_only_the_malformed_client(reliability_server) -> None:
    _server, _thread, address = reliability_server
    malformed = socket.create_connection(address, timeout=3)
    good = Peer(address)
    try:
        malformed.sendall(b"x" * (MAX_MESSAGE_SIZE + 1) + b"\n")
        response_buffer = MessageBuffer()
        responses: list[dict] = []
        while not responses:
            responses.extend(response_buffer.feed(malformed.recv(4096)))
        assert responses[0]["type"] == "protocol_error"
        good.send("echo", "good-client", "still usable")
        assert good.receive()["payload"] == "still usable"
    finally:
        malformed.close()
        good.close()


def test_concurrent_drawing_requests_get_unique_authoritative_sequences(
    reliability_server,
) -> None:
    server, _thread, address = reliability_server
    peers = [Peer(address) for _ in range(3)]
    try:
        room = enter_room(peers[0])
        enter_room(peers[1], room, "Bob")
        peers[0].receive()
        enter_room(peers[2], room, "Charlie")
        peers[0].receive()
        peers[1].receive()

        def submit_batch(peer_index: int) -> None:
            for number in range(8):
                peers[peer_index].send(
                    "drawing_operation",
                    f"{peer_index}-{number}",
                    {
                        "operation_type": "pen",
                        "points": [[peer_index, number]],
                        "color": "#112233",
                        "brush_size": 3,
                    },
                )

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(submit_batch, peer_index) for peer_index in range(3)]
            for future in futures:
                future.result()

        acknowledgements: list[int] = []
        for peer in peers:
            messages = [peer.receive() for _ in range(24)]
            acknowledgements.extend(
                message["payload"]["sequence_number"]
                for message in messages
                if message["type"] == "drawing_accepted"
            )
        assert len(acknowledgements) == 24
        assert sorted(acknowledgements) == list(range(1, 25))
        stored = server.sessions.get_drawing_operations(room)
        assert [operation["sequence_number"] for operation in stored] == list(range(1, 25))
    finally:
        for peer in peers:
            peer.close()


def test_server_enforces_room_membership_and_operation_limit(
    reliability_server, monkeypatch
) -> None:
    _server, _thread, address = reliability_server
    from server import session_manager

    monkeypatch.setattr(session_manager, "MAX_ROOM_OPERATIONS", 1)
    host, outsider = Peer(address), Peer(address)
    try:
        room = enter_room(host)
        for kind, payload in (
            ("drawing_operation", {"operation_type": "pen", "points": [[1, 2]], "color": "#112233", "brush_size": 2}),
            ("chat_message", {"text": "no room"}),
            ("cursor_update", {"x": 0.5, "y": 0.5}),
            ("undo_operation", {}),
            ("redo_operation", {}),
            ("save_board", {}),
            ("load_board", {}),
        ):
            outsider.send(kind, f"unauthorized-{kind}", payload)
            error = outsider.receive()
            assert error["type"] == "error"
            assert error["payload"]["code"] == "not_in_room"

        host.send(
            "drawing_operation",
            "first",
            {"operation_type": "pen", "points": [[1, 2]], "color": "#112233", "brush_size": 2},
        )
        assert host.receive()["type"] == "drawing_accepted"
        host.send(
            "drawing_operation",
            "over-limit",
            {"operation_type": "pen", "points": [[2, 3]], "color": "#112233", "brush_size": 2},
        )
        assert host.receive()["payload"]["code"] == "operation_limit"
        assert len(_server.sessions.get_drawing_operations(room)) == 1
    finally:
        host.close()
        outsider.close()


def test_server_caps_total_room_snapshot_size(reliability_server, monkeypatch) -> None:
    server, _thread, address = reliability_server
    from server import session_manager

    host = Peer(address)
    try:
        room = enter_room(host)
        current_size = server.sessions._rooms[room].drawing_state_bytes
        monkeypatch.setattr(session_manager, "MAX_MESSAGE_SIZE", current_size + 1)
        host.send(
            "drawing_operation",
            "too-large-for-snapshot",
            {"operation_type": "pen", "points": [[1, 2]], "color": "#123456", "brush_size": 3},
        )
        response = host.receive()
        assert response["type"] == "error"
        assert response["payload"]["code"] == "operation_limit"
        assert server.sessions.get_drawing_operations(room) == []
    finally:
        host.close()


def test_network_client_heartbeats_and_reconnects_with_board_snapshot(
    tmp_path,
) -> None:
    application = QApplication.instance() or QApplication([])
    server = TCPServer(
        "127.0.0.1", 0, boards_directory=tmp_path, reconnect_grace_period=3
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    assert server.wait_until_ready(timeout=3)
    assert server.server_address is not None
    address, port = server.server_address

    client = NetworkClient(
        address,
        port,
        "create_room",
        {"username": "Recovery"},
        heartbeat_interval=0.1,
        heartbeat_timeout=0.6,
        reconnect_delays=(0.05, 0.1, 0.2),
    )
    messages: list[dict] = []
    client.message_received.connect(messages.append)
    client.start()

    def wait_for(predicate, timeout=4) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            application.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        assert predicate(), "network client did not reach the expected state"

    try:
        wait_for(lambda: any(message["type"] == "room_created" for message in messages))
        room_message = next(message for message in messages if message["type"] == "room_created")
        room_code = room_message["payload"]["room_code"]
        participant_id = room_message["payload"]["participant_id"]
        wait_for(lambda: any(message["type"] == "pong" for message in messages))
        assert client.send_drawing_operation(
            "stroke",
            {"operation_type": "pen", "points": [[4, 5]], "color": "#112233", "brush_size": 3},
        )
        wait_for(lambda: any(message["type"] == "drawing_accepted" for message in messages))

        old_client_ids = list(server._clients)
        assert len(old_client_ids) == 1
        server._close_client(old_client_ids[0])
        wait_for(lambda: any(message["type"] == "room_joined" for message in messages))
        wait_for(
            lambda: any(
                message["type"] == "board_state"
                and message["payload"].get("room_code") == room_code
                and len(message["payload"].get("operations", [])) == 1
                for message in messages
            )
        )
        joined = next(message for message in messages if message["type"] == "room_joined")
        assert joined["payload"]["participant_id"] == participant_id
        snapshot = next(
            message for message in reversed(messages)
            if message["type"] == "board_state" and message["payload"].get("room_code") == room_code
        )
        assert snapshot["payload"]["operations"][0]["points"] == [[4, 5]]
    finally:
        client.close()
        server.shutdown()
        server_thread.join(timeout=3)
        assert not server_thread.is_alive()


def test_network_client_recovers_saved_room_after_server_restart(tmp_path) -> None:
    application = QApplication.instance() or QApplication([])
    first_server = TCPServer("127.0.0.1", 0, boards_directory=tmp_path)
    first_thread = threading.Thread(target=first_server.serve_forever, daemon=True)
    first_thread.start()
    assert first_server.wait_until_ready(timeout=3)
    assert first_server.server_address is not None
    address, port = first_server.server_address
    client = NetworkClient(
        address,
        port,
        "create_room",
        {"username": "Restarting"},
        heartbeat_interval=0.1,
        heartbeat_timeout=0.6,
        reconnect_delays=(0.05, 0.1, 0.2),
    )
    messages: list[dict] = []
    client.message_received.connect(messages.append)
    client.start()

    def wait_for(predicate, timeout=4) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            application.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        assert predicate(), "network client did not recover the saved room"

    second_server: TCPServer | None = None
    second_thread: threading.Thread | None = None
    try:
        wait_for(lambda: any(message["type"] == "room_created" for message in messages))
        created = next(message for message in messages if message["type"] == "room_created")
        room_code = created["payload"]["room_code"]
        participant_id = created["payload"]["participant_id"]
        client.send_drawing_operation(
            "persist-me",
            {"operation_type": "pen", "points": [[7, 8]], "color": "#123456", "brush_size": 3},
        )
        wait_for(lambda: any(message["type"] == "drawing_accepted" for message in messages))
        assert client.send_message("save_board", {})
        wait_for(lambda: any(message["type"] == "board_saved" for message in messages))

        first_server.shutdown()
        first_thread.join(timeout=3)
        assert not first_thread.is_alive()

        second_server = TCPServer(
            "127.0.0.1", port, boards_directory=tmp_path, reconnect_grace_period=3
        )
        second_thread = threading.Thread(target=second_server.serve_forever, daemon=True)
        second_thread.start()
        assert second_server.wait_until_ready(timeout=3)
        wait_for(lambda: any(message["type"] == "room_joined" for message in messages))
        wait_for(
            lambda: any(
                message["type"] == "board_state"
                and message["payload"].get("room_code") == room_code
                and len(message["payload"].get("operations", [])) == 1
                for message in messages
            )
        )
        joined = next(message for message in messages if message["type"] == "room_joined")
        assert joined["payload"]["participant_id"] == participant_id
        state = next(
            message for message in reversed(messages)
            if message["type"] == "board_state" and message["payload"].get("room_code") == room_code
        )
        assert state["payload"]["operations"][0]["points"] == [[7, 8]]
    finally:
        client.close()
        if second_server is not None:
            second_server.shutdown()
        if second_thread is not None:
            second_thread.join(timeout=3)
            assert not second_thread.is_alive()
        if first_thread.is_alive():
            first_server.shutdown()
            first_thread.join(timeout=3)
        assert not first_thread.is_alive()
