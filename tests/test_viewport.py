"""Rendering and mouse-interaction coverage for local viewport controls."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from client.drawing_canvas import DrawingCanvas
from client.whiteboard_page import WhiteboardPage
from server.persistence import PersistenceManager


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def wheel(canvas, position: QPointF, delta: int) -> None:
    event = QWheelEvent(
        position,
        canvas.mapToGlobal(position.toPoint()),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(canvas, event)


def test_wheel_zoom_limits_anchor_and_reset(app):
    canvas = DrawingCanvas()
    canvas.resize(300, 240)
    anchor = QPointF(123, 88)
    world_before = canvas.screen_to_world(anchor)
    wheel(canvas, anchor, 120)
    assert canvas.zoom == pytest.approx(1.2)
    world_after = canvas.screen_to_world(anchor)
    assert world_after.x() == pytest.approx(world_before.x())
    assert world_after.y() == pytest.approx(world_before.y())

    for _ in range(30):
        canvas.zoom_in()
    assert canvas.zoom == canvas.MAX_ZOOM
    for _ in range(50):
        canvas.zoom_out()
    assert canvas.zoom == canvas.MIN_ZOOM
    canvas.pan_x, canvas.pan_y = 42, -17
    canvas.reset_view()
    assert (canvas.zoom, canvas.pan_x, canvas.pan_y) == (1.0, 0.0, 0.0)
    canvas.close()


def test_middle_drag_pans_without_drawing_and_controls_show_zoom(app):
    canvas = DrawingCanvas()
    canvas.resize(240, 180)
    canvas.show()
    app.processEvents()
    QTest.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(80, 70))
    QTest.mouseMove(canvas, QPoint(110, 100), delay=5)
    QTest.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=QPoint(110, 100))
    assert canvas.pan_x == pytest.approx(-30)
    assert canvas.pan_y == pytest.approx(-30)
    assert canvas.engine.operations == []

    page = WhiteboardPage()
    QTest.mouseClick(page.zoom_in_button, Qt.MouseButton.LeftButton)
    assert page.zoom_label.text() == "120%"
    QTest.mouseClick(page.reset_view_button, Qt.MouseButton.LeftButton)
    assert page.zoom_label.text() == "100%"
    page.close()
    canvas.close()


@pytest.mark.parametrize("tool", ["pen", "eraser", "line", "rectangle", "ellipse"])
@pytest.mark.parametrize("brush", ["pen", "pencil", "marker", "highlighter"])
def test_mouse_draws_with_transformed_logical_coordinates_and_brushes(app, tool, brush):
    canvas = DrawingCanvas()
    canvas.setMinimumSize(0, 0)
    canvas.resize(260, 220)
    canvas.set_pen_color("#e53935")
    canvas.set_brush_size(6)
    canvas.set_brush_type(brush)
    canvas.set_tool(tool)
    canvas.zoom = 2.0
    canvas.pan_x, canvas.pan_y = 10.0, 20.0
    canvas.show()
    app.processEvents()

    if tool == "eraser":
        canvas.set_tool("pen")
        canvas.set_brush_size(24)
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
        QTest.mouseMove(canvas, QPoint(140, 120), delay=5)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(140, 120))
        canvas.set_brush_size(6)
        canvas.set_tool("eraser")

    # Screen (100, 100) maps to logical (60, 70); zoom does not alter stored coordinates.
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    QTest.mouseMove(canvas, QPoint(140, 120), delay=5)
    preview = canvas.engine.current_operation
    assert preview is not None
    assert preview.points[-1] == pytest.approx((80.0, 80.0))
    preview_image = canvas.grab().toImage()
    assert any(
        preview_image.pixelColor(x, y) != QColor("white")
        for x in range(75, 166) for y in range(75, 146)
    )
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(140, 120))
    operation = canvas.engine.operations[-1]
    assert operation.points[0] == pytest.approx((60.0, 70.0))
    assert operation.points[-1] == pytest.approx((80.0, 80.0))
    assert operation.brush_type == ("pen" if tool == "eraser" else brush)
    assert operation.brush_size == 6

    image = canvas.grab().toImage()
    screen = canvas.world_to_screen(QPointF(70, 75))
    px, py = round(screen.x()), round(screen.y())
    if tool == "eraser":
        assert image.pixelColor(px, py) == QColor("white")
    else:
        assert any(
            image.pixelColor(x, y) != QColor("white")
            for x in range(image.width()) for y in range(image.height())
        )
    canvas.close()


def test_remote_cursor_uses_logical_position_and_png_ignores_viewport(app, tmp_path):
    canvas = DrawingCanvas()
    canvas.setMinimumSize(0, 0)
    canvas.resize(200, 160)
    canvas.zoom, canvas.pan_x, canvas.pan_y = 2.0, 10.0, 20.0
    cursor_world = QPointF(60, 70)
    canvas.set_remote_cursor({
        "participant_id": "p", "username": "Remote", "color": "#00aa00",
        "x": cursor_world.x() / (canvas.width() - 1),
        "y": cursor_world.y() / (canvas.height() - 1),
    })
    cursor_screen = canvas.world_to_screen(cursor_world)
    canvas.show()
    app.processEvents()
    screen_image = canvas.grab().toImage()
    assert screen_image.pixelColor(round(cursor_screen.x()), round(cursor_screen.y())) == QColor("#00aa00")

    canvas.engine.begin_stroke((60, 70), "#e53935", 8)
    canvas.engine.finish_stroke((90, 70))
    target = tmp_path / "viewport-independent.png"
    assert canvas.export_png(str(target))
    exported = QImage(str(target))
    assert exported.size() == canvas.size()
    assert exported.pixelColor(75, 70) == QColor("#e53935")
    assert exported.pixelColor(round(canvas.world_to_screen(QPointF(75, 70)).x()), round(canvas.world_to_screen(QPointF(75, 70)).y())) == QColor("white")
    canvas.close()


def test_cursor_network_coordinates_are_world_based_across_local_views(app):
    sender, receiver = DrawingCanvas(), DrawingCanvas()
    for canvas in (sender, receiver):
        canvas.setMinimumSize(0, 0)
        canvas.resize(200, 160)
    sender.zoom, sender.pan_x, sender.pan_y = 2.0, 10.0, 20.0
    receiver.zoom, receiver.pan_x, receiver.pan_y = 0.75, -5.0, 12.0
    sender.show()
    receiver.show()
    app.processEvents()
    emitted = []
    sender.cursor_moved.connect(lambda x, y: emitted.append((x, y)))

    screen = sender.world_to_screen(QPointF(60, 70))
    screen_point = QPoint(round(screen.x()), round(screen.y()))
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(screen_point),
        QPointF(sender.mapToGlobal(screen_point)),
        Qt.MouseButton.NoButton,
        Qt.MouseButtons(),
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(sender, event)
    assert emitted[-1][0] == pytest.approx(60 / (sender.width() - 1), abs=.01)
    assert emitted[-1][1] == pytest.approx(70 / (sender.height() - 1), abs=.01)
    receiver.set_remote_cursor({
        "participant_id": "sender", "username": "Sender", "color": "#00aa00",
        "x": emitted[-1][0], "y": emitted[-1][1],
    })
    expected_screen = receiver.world_to_screen(QPointF(
        emitted[-1][0] * (receiver.width() - 1),
        emitted[-1][1] * (receiver.height() - 1),
    ))
    receiver_image = receiver.grab().toImage()
    assert receiver_image.pixelColor(round(expected_screen.x()), round(expected_screen.y())) == QColor("#00aa00")
    sender.close()
    receiver.close()


def test_undo_redo_and_board_replacement_keep_logical_points_after_view_changes(app, tmp_path):
    canvas = DrawingCanvas()
    payload = {
        "operation_type": "pen", "points": [[12, 18], [28, 32]], "color": "#123456",
        "brush_size": 5, "brush_type": "marker", "opacity": 0.72,
        "sequence_number": 1, "active": True,
    }
    canvas.add_remote_operation(payload)
    canvas.zoom_in()
    canvas.pan_x, canvas.pan_y = 80, 40
    assert canvas.set_operation_active(1, False)
    assert canvas.engine.operations[0].points == [(12.0, 18.0), (28.0, 32.0)]
    assert canvas.set_operation_active(1, True)
    manager = PersistenceManager(tmp_path)
    manager.save_board("ABCD23", [{
        **{key: value for key, value in payload.items() if key not in {"sequence_number", "active"}},
        "sequence_number": 1, "participant_id": "p1", "room_code": "ABCD23", "active": True,
    }])
    restored = manager.load_board("ABCD23")
    canvas.replace_board_state(restored.operations)
    operation = canvas.engine.operations[0]
    assert operation.brush_type == "marker" and operation.opacity == 0.72
    assert operation.points == [(12.0, 18.0), (28.0, 32.0)]
    canvas.close()
