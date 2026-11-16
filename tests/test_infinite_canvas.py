"""Infinite-world coordinate, culling, collaboration, and export tests."""

import math
import socket
import threading

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from client.drawing_canvas import (
    EMPTY_EXPORT_SIZE,
    MAX_EXPORT_DIMENSION,
    DrawingCanvas,
)
from client.drawing_engine import DrawingEngine
from server.persistence import PersistenceManager
from server.tcp_server import TCPServer
from shared.protocol import MessageBuffer, encode_message


@pytest.fixture(scope="module")
def app():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application


class Peer:
    def __init__(self, address):
        self.socket = socket.create_connection(address, timeout=3)
        self.socket.settimeout(3)
        self.buffer = MessageBuffer()
        self.pending = []

    def send(self, kind, request_id, payload):
        self.socket.sendall(encode_message(kind, request_id, payload))

    def receive(self):
        while not self.pending:
            data = self.socket.recv(4096)
            if not data:
                raise AssertionError("server disconnected before the expected message")
            self.pending.extend(self.buffer.feed(data))
        return self.pending.pop(0)

    def close(self):
        self.socket.close()


@pytest.fixture
def running_server(tmp_path):
    server = TCPServer("127.0.0.1", 0, boards_directory=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(3)
    yield server, server.server_address
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_real_mouse_drawing_reaches_negative_and_far_positive_world(app):
    canvas = DrawingCanvas()
    canvas.setMinimumSize(0, 0)
    canvas.resize(300, 240)
    canvas.show()
    app.processEvents()

    # The pointer remains inside the widget while the viewport targets distant world regions.
    for pan_x, pan_y, expected in ((-1100, -900, (-1000, -800)), (49_900, 79_900, (50_000, 80_000))):
        canvas.pan_x, canvas.pan_y = pan_x, pan_y
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
        QTest.mouseMove(canvas, QPoint(150, 140), delay=5)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(150, 140))
        operation = canvas.engine.operations[-1]
        assert operation.points[0] == pytest.approx(expected)
        world_screen = canvas.world_to_screen(QPointF(*expected))
        image = canvas.grab().toImage()
        assert image.pixelColor(round(world_screen.x()), round(world_screen.y())) != QColor("white")
    canvas.close()


def test_negative_world_cursor_maps_to_panned_view(app):
    canvas = DrawingCanvas()
    canvas.setMinimumSize(0, 0)
    canvas.resize(220, 180)
    canvas.zoom = 1.5
    canvas.pan_x, canvas.pan_y = -5_100, 2_900
    cursor_world = (-5_000, 3_000)
    canvas.set_remote_cursor({
        "participant_id": "remote", "username": "Remote", "color": "#00aa00",
        "x": cursor_world[0], "y": cursor_world[1], "coordinate_space": "world",
    })
    canvas.show()
    app.processEvents()
    screen = canvas.world_to_screen(QPointF(*cursor_world))
    assert canvas.grab().toImage().pixelColor(round(screen.x()), round(screen.y())) == QColor("#00aa00")
    canvas.close()


def test_spatial_query_culls_history_and_keeps_crossing_and_large_operations():
    engine = DrawingEngine()
    for index in range(5_000):
        x = index * 1_000
        engine.begin_stroke((x, 0), "#123456", 4)
        engine.finish_stroke((x + 10, 0))
    engine.begin_stroke((-100_000, 100), "#654321", 4)
    engine.finish_stroke((100_000, 100))  # global bucket for a very large extent

    visible = engine.visible_operations((-40, -40, 40, 140))
    assert len(engine.operations) == 5_001
    assert engine.operations[0] in visible  # crosses a cell boundary at x=0
    assert engine.operations[-1] in visible  # broad operation intersects this viewport
    assert len(visible) == 2

    restored = DrawingEngine()
    snapshot = [
        {
            "operation_type": "pen", "points": [[index * 1_000, 5], [index * 1_000 + 10, 5]],
            "color": "#123456", "brush_size": 4, "sequence_number": index + 1,
        }
        for index in range(5_000)
    ]
    restored.replace_authoritative_state(snapshot)
    around_last = restored.visible_operations((4_998_950, -20, 4_999_050, 30))
    assert len(restored.operations) == 5_000
    assert len(around_last) == 1
    assert around_last[0].sequence_number == 5_000


@pytest.mark.parametrize("operation_type", ["pen", "line", "rectangle", "ellipse"])
def test_partially_visible_strokes_and_shapes_are_clipped_not_culled(app, operation_type):
    canvas = DrawingCanvas()
    canvas.setMinimumSize(0, 0)
    canvas.resize(120, 100)
    points = [[-40, 30], [60, 70]] if operation_type in {"line", "rectangle", "ellipse"} else [
        [-40, 30], [0, 50], [60, 70]
    ]
    payload = {
        "operation_type": operation_type, "points": points, "color": "#e53935",
        "brush_size": 8, "sequence_number": 1,
    }
    canvas.add_remote_operation(payload)
    canvas.show()
    app.processEvents()
    image = canvas.grab().toImage()
    assert any(
        image.pixelColor(x, y) == QColor("#e53935")
        for x in range(image.width()) for y in range(image.height())
    )
    canvas.close()


def test_undo_redo_stays_indexed_at_distant_negative_coordinates():
    engine = DrawingEngine()
    operation = {
        "operation_type": "line", "points": [[-500_000, 800_000], [-499_900, 800_100]],
        "color": "#123456", "brush_size": 8, "sequence_number": 1,
    }
    assert engine.add_remote_operation(operation)
    area = (-500_100, 799_900, -499_800, 800_200)
    assert engine.visible_operations(area) == engine.operations
    assert engine.set_operation_active(1, False)
    assert not engine.operations[0].active
    assert engine.set_operation_active(1, True)
    assert engine.operations[0].active
    assert engine.visible_operations(area) == engine.operations


def test_export_uses_negative_world_bounds_empty_board_and_size_safety(app, tmp_path):
    canvas = DrawingCanvas()
    canvas.setMinimumSize(0, 0)
    canvas.resize(300, 220)
    canvas.engine.begin_stroke((-200, -100), "#e53935", 8)
    canvas.engine.finish_stroke((-160, -100))
    canvas.engine.begin_stroke((800, 900), "#3867d6", 12)
    canvas.engine.finish_stroke((850, 900))
    canvas.zoom, canvas.pan_x, canvas.pan_y = 4, 100_000, -90_000
    target = tmp_path / "whole-world.png"
    assert canvas.export_png(str(target))
    image = QImage(str(target))
    assert image.width() > 1_000 and image.height() > 1_000
    bounds = canvas.engine.operations_bounds(canvas.engine.operations)
    left = math.floor(bounds[0] - 24)
    top = math.floor(bounds[1] - 24)
    assert image.pixelColor(-180 - left, -100 - top) == QColor("#e53935")
    assert image.pixelColor(825 - left, 900 - top) == QColor("#3867d6")

    canvas.clear()
    empty_path = tmp_path / "empty.png"
    assert canvas.export_png(str(empty_path))
    empty = QImage(str(empty_path))
    assert (empty.width(), empty.height()) == EMPTY_EXPORT_SIZE
    canvas.engine.begin_stroke((-MAX_EXPORT_DIMENSION, 0), "#000000", 4)
    canvas.engine.finish_stroke((MAX_EXPORT_DIMENSION, 0))
    with pytest.raises(ValueError, match="PNG export is too large"):
        canvas.export_png(str(tmp_path / "too-large.png"))
    canvas.close()


def test_world_drawing_cursor_snapshots_and_saved_state_keep_coordinates(running_server, tmp_path):
    server, address = running_server
    alice, bob = Peer(address), Peer(address)
    try:
        alice.send("create_room", "create", {"username": "Alice"})
        room = alice.receive()["payload"]["room_code"]
        alice.receive()
        bob.send("join_room", "join", {"username": "Bob", "room_code": room})
        bob.receive(); bob.receive(); bob.receive()  # joined, roster, full initial snapshot
        alice.receive()

        operation = {
            "operation_type": "line", "points": [[-25_000, -40_000], [2_000_000, 3_000_000]],
            "color": "#123456", "brush_size": 8, "brush_type": "marker", "opacity": .72,
        }
        alice.send("drawing_operation", "distant", operation)
        accepted = alice.receive()
        received = bob.receive()
        assert accepted["type"] == "drawing_accepted"
        assert received["payload"]["points"] == operation["points"]
        assert received["payload"]["brush_type"] == "marker"

        alice.send("cursor_update", "cursor", {
            "x": -25_000, "y": 3_000_000, "coordinate_space": "world",
        })
        cursor = bob.receive()
        assert cursor["payload"]["x"] == -25_000
        assert cursor["payload"]["y"] == 3_000_000
        assert cursor["payload"]["coordinate_space"] == "world"
        alice.send("cursor_update", "bad-world-cursor", {
            "x": 10_000_001, "y": 0, "coordinate_space": "world",
        })
        invalid_cursor = alice.receive()
        assert invalid_cursor["type"] == "error"
        assert invalid_cursor["payload"]["code"] == "invalid_cursor"

        charlie = Peer(address)
        charlie.send("join_room", "late-join", {"username": "Charlie", "room_code": room})
        charlie.receive(); charlie.receive()
        late_snapshot = charlie.receive()
        assert late_snapshot["type"] == "board_state"
        assert late_snapshot["payload"]["operations"][0]["points"] == operation["points"]
        charlie.close()

        snapshot = server.sessions.get_drawing_operations(room)
        owner = snapshot[0]["participant_id"]
        save = PersistenceManager(tmp_path).save_board(
            room,
            snapshot,
            participant_names={owner: "Alice"},
        )
        restored = PersistenceManager(tmp_path).load_board(room)
        assert restored.operations[0]["points"] == operation["points"]
        assert restored.operations[0]["brush_type"] == "marker"
        assert save.operations[0]["points"] == operation["points"]
    finally:
        alice.close()
        bob.close()


def test_far_world_coordinates_survive_server_restart_and_rejoin(tmp_path):
    def start_server():
        instance = TCPServer("127.0.0.1", 0, boards_directory=tmp_path, reconnect_grace_period=0)
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        assert instance.wait_until_ready(3)
        return instance, thread, instance.server_address

    first, first_thread, first_address = start_server()
    host = Peer(first_address)
    room = None
    try:
        host.send("create_room", "create", {"username": "Persisted"})
        room = host.receive()["payload"]["room_code"]
        host.receive()
        points = [[-900_000, -700_000], [4_000_000, 2_000_000]]
        host.send("drawing_operation", "far-op", {
            "operation_type": "line", "points": points, "color": "#123456",
            "brush_size": 6, "brush_type": "highlighter", "opacity": .28,
        })
        host.receive()
        host.send("save_board", "save", {})
        assert host.receive()["type"] == "board_saved"
    finally:
        host.close()
        first.shutdown()
        first_thread.join(timeout=3)
        assert not first_thread.is_alive()

    second, second_thread, second_address = start_server()
    restored = Peer(second_address)
    try:
        restored.send("join_room", "rejoin", {"username": "Reader", "room_code": room})
        assert restored.receive()["type"] == "room_joined"
        restored.receive()
        snapshot = restored.receive()
        operation = snapshot["payload"]["operations"][0]
        assert operation["points"] == points
        assert operation["brush_type"] == "highlighter"
        assert operation["opacity"] == .28
    finally:
        restored.close()
        second.shutdown()
        second_thread.join(timeout=3)
        assert not second_thread.is_alive()
