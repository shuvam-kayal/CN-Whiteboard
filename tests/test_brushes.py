"""Slice 13 brush validation, rendering, GUI, and wire-format coverage."""

import os
import math
import socket
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from client.drawing_canvas import DrawingCanvas
from client.drawing_engine import DrawingEngine
from client.whiteboard_page import WhiteboardPage
from server.persistence import PersistenceManager
from server.tcp_server import TCPServer
from shared.drawing import DrawingDataError, validate_drawing_operation
from shared.protocol import MessageBuffer, encode_message


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_legacy_operation_defaults_and_new_brush_validation():
    legacy = validate_drawing_operation(
        {"operation_type": "pen", "points": [[1, 2]], "color": "#123456", "brush_size": 4}
    )
    assert legacy["brush_type"] == "pen" and legacy["opacity"] == 1.0
    good = validate_drawing_operation(
        {
            "operation_type": "pen", "points": [[1, 2]], "color": "#123456",
            "brush_size": 128, "brush_type": "highlighter", "opacity": 0.28,
        }
    )
    assert good["brush_type"] == "highlighter" and good["opacity"] == 0.28
    for field, value in (("brush_type", "spray"), ("opacity", 1.1), ("opacity", 0), ("brush_size", 129), ("color", "nope")):
        payload = {
            "operation_type": "pen", "points": [[1, 2]], "color": "#123456",
            "brush_size": 4, "brush_type": "pen", "opacity": 1.0,
        }
        payload[field] = value
        with pytest.raises(DrawingDataError):
            validate_drawing_operation(payload)


def test_toolbar_preset_custom_color_brush_and_size_controls(app):
    board = WhiteboardPage()
    colors = {board.color_combo.itemData(i) for i in range(board.color_combo.count())}
    assert {"#000000", "#ffffff", "#e53935", "#279777", "#3867d6", "#fdd835", "#dd812f", "#8e24aa", "#ec407a"} <= colors
    board.color_combo.setCurrentIndex(board.color_combo.findData("#e53935"))
    assert board.canvas.pen_color == "#e53935"
    board.set_selected_color("#12abef")
    assert board.canvas.pen_color == "#12abef"
    with pytest.raises(ValueError):
        board.set_selected_color("not a color")
    board.brush_combo.setCurrentIndex(board.brush_combo.findData("highlighter"))
    board.size_combo.setCurrentIndex(board.size_combo.findData(128))
    assert board.canvas.brush_type == "highlighter"
    assert board.canvas.brush_size == 128
    board.close()


@pytest.mark.parametrize(
    ("brush", "expected_opacity"),
    [("pen", 1.0), ("pencil", 0.62), ("marker", 0.72), ("highlighter", 0.28)],
)
def test_brush_operations_capture_render_properties_and_export(app, tmp_path, brush, expected_opacity):
    canvas = DrawingCanvas()
    canvas.setMinimumSize(0, 0)
    canvas.resize(160, 100)
    canvas.set_brush_type(brush)
    canvas.set_pen_color("#e53935")
    canvas.set_brush_size(10)
    canvas.engine.begin_stroke((20, 50), canvas.pen_color, canvas.brush_size, brush_type=canvas.brush_type, opacity=canvas.brush_opacity)
    canvas.engine.finish_stroke((130, 50))
    operation = canvas.engine.operations[0]
    assert operation.brush_type == brush
    assert operation.opacity == expected_opacity
    target = tmp_path / f"{brush}.png"
    assert canvas.export_png(str(target))
    image = QImage(str(target))
    bounds = canvas.engine.operations_bounds(canvas.engine.operations)
    left, top = math.floor(bounds[0] - 24), math.floor(bounds[1] - 24)
    right, bottom = math.ceil(bounds[2] + 24), math.ceil(bounds[3] + 24)
    assert image.size().width() == right - left + 1
    assert image.size().height() == bottom - top + 1
    assert image.pixelColor(70 - left, 50 - top) != QColor("white")
    assert operation.to_payload()["brush_type"] == brush
    canvas.close()


def test_highlighter_blends_and_undo_redo_preserves_brush_metadata(app):
    engine = DrawingEngine()
    for brush, opacity in (("pen", 1), ("pencil", .62), ("marker", .72), ("highlighter", .28)):
        engine.begin_stroke((5, 10), "#ff0000", 8, brush_type=brush, opacity=opacity)
        operation = engine.finish_stroke((25, 10))
        operation.sequence_number = len(engine.operations)
    canvas = DrawingCanvas()
    canvas.setMinimumSize(0, 0)
    canvas.resize(40, 30)
    canvas.engine = engine
    canvas.engine.begin_stroke((5, 10), "#0000ff", 10)
    canvas.engine.finish_stroke((25, 10))
    # Re-render the final operation with highlighter settings over the red strokes.
    last = canvas.engine.operations[-1]
    last.brush_type, last.opacity = "highlighter", .28
    image = canvas.grab().toImage()
    blended = image.pixelColor(15, 10)
    assert blended.blue() > 0 and blended.red() > 0
    for index, brush in enumerate(("pen", "pencil", "marker", "highlighter"), start=1):
        assert engine.set_operation_active(index, False)
        assert engine.set_operation_active(index, True)
        assert engine.operations[index - 1].brush_type == brush
    canvas.close()


def test_brush_properties_round_trip_and_legacy_board_defaults(tmp_path):
    manager = PersistenceManager(tmp_path)
    op = {
        "operation_type": "pen", "points": [[1, 2]], "color": "#112233", "brush_size": 9,
        "brush_type": "marker", "opacity": .72, "sequence_number": 1,
        "participant_id": "p1", "room_code": "ABCD23", "active": True,
    }
    saved = manager.save_board("ABCD23", [op])
    assert manager.load_board("ABCD23").operations == saved.operations
    legacy_manager = PersistenceManager(tmp_path / "legacy")
    legacy_manager.boards_directory.mkdir()
    import json
    (legacy_manager.boards_directory / "ABCD23.json").write_text(json.dumps({
        "format_version": 1, "room_code": "ABCD23", "saved_at": "2026-11-16T00:00:00Z",
        "operations": [{k: v for k, v in op.items() if k not in {"brush_type", "opacity", "active"}}],
    }), encoding="utf-8")
    restored = legacy_manager.load_board("ABCD23").operations[0]
    assert restored["brush_type"] == "pen" and restored["opacity"] == 1.0


class Peer:
    def __init__(self, address):
        self.sock = socket.create_connection(address, timeout=2)
        self.sock.settimeout(2)
        self.buffer = MessageBuffer()
        self.pending = []

    def send(self, kind, request, payload):
        self.sock.sendall(encode_message(kind, request, payload))

    def recv(self):
        while not self.pending:
            data = self.sock.recv(4096)
            if not data:
                raise AssertionError("server disconnected")
            self.pending.extend(self.buffer.feed(data))
        return self.pending.pop(0)

    def close(self):
        self.sock.close()


def test_brush_properties_are_validated_broadcast_and_persisted_over_tcp(tmp_path):
    server = TCPServer("127.0.0.1", 0, boards_directory=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.wait_until_ready(3)
    assert server.server_address is not None
    host, guest = Peer(server.server_address), Peer(server.server_address)
    try:
        host.send("create_room", "c", {"username": "Host"})
        created = host.recv()
        code = created["payload"]["room_code"]
        host.recv()
        guest.send("join_room", "j", {"username": "Guest", "room_code": code})
        guest.recv(); guest.recv(); guest.recv()
        host.recv()
        operation = {
            "operation_type": "pen", "points": [[10, 20], [30, 40]], "color": "#345678",
            "brush_size": 12, "brush_type": "highlighter", "opacity": .28,
        }
        host.send("drawing_operation", "d1", operation)
        assert host.recv()["type"] == "drawing_accepted"
        delivered = guest.recv()["payload"]
        assert {key: delivered[key] for key in operation} == operation
        for invalid in ({**operation, "brush_type": "spray"}, {**operation, "opacity": 2}, {**operation, "brush_size": 129}):
            host.send("drawing_operation", "bad", invalid)
            assert host.recv()["type"] == "error"
        stored = server.sessions.get_drawing_operations(code)
        assert stored[0]["brush_type"] == "highlighter" and stored[0]["opacity"] == .28
    finally:
        host.close(); guest.close()
        server.shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()
