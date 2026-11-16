import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from client.drawing_canvas import DrawingCanvas
from client.drawing_engine import DrawingEngine, DrawingOperation


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_engine_stores_completed_operation_data() -> None:
    engine = DrawingEngine()
    engine.begin_stroke((10, 12), "#3867d6", 6, "pen")
    assert engine.operations == []
    engine.add_point((30, 32))
    operation = engine.finish_stroke((50, 52))

    assert operation == DrawingOperation(
        "pen", [(10.0, 12.0), (30.0, 32.0), (50.0, 52.0)], "#3867d6", 6
    )
    assert engine.operations == [operation]


def test_engine_applies_authoritative_undo_redo_status() -> None:
    engine = DrawingEngine()
    assert engine.add_remote_operation(
        {
            "operation_type": "eraser",
            "points": [[1, 2]],
            "color": "#ffffff",
            "brush_size": 8,
            "sequence_number": 1,
            "active": False,
        }
    )
    assert engine.operations[0].active is False
    assert engine.set_operation_active(1, True)
    assert engine.operations[0].active is True


def test_canvas_records_mouse_strokes_and_renders_them(app) -> None:
    canvas = DrawingCanvas()
    canvas.resize(460, 440)
    canvas.set_pen_color("#3867d6")
    canvas.set_brush_size(8)
    canvas.show()
    app.processEvents()

    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(40, 50))
    QTest.mouseMove(canvas, QPoint(130, 95), delay=10)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(130, 95))
    app.processEvents()

    assert len(canvas.engine.operations) == 1
    stroke = canvas.engine.operations[0]
    assert stroke.operation_type == "pen"
    assert stroke.color == "#3867d6"
    assert stroke.brush_size == 8
    assert len(stroke.points) >= 2
    image = canvas.grab().toImage()
    assert image.pixelColor(85, 72) == QColor("#3867d6")
    canvas.close()


def test_eraser_and_clear_are_recorded_and_repainted(app) -> None:
    canvas = DrawingCanvas()
    canvas.resize(460, 440)
    canvas.show()
    app.processEvents()

    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(40, 80))
    QTest.mouseMove(canvas, QPoint(150, 80), delay=10)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(150, 80))

    canvas.set_tool("eraser")
    canvas.set_brush_size(24)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(95, 50))
    QTest.mouseMove(canvas, QPoint(95, 110), delay=10)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(95, 110))
    app.processEvents()

    assert [op.operation_type for op in canvas.engine.operations] == ["pen", "eraser"]
    assert canvas.engine.operations[1].color == "#ffffff"
    assert canvas.grab().toImage().pixelColor(95, 80) == QColor("white")

    canvas.clear()
    assert canvas.engine.operations == []
    assert canvas.grab().toImage().pixelColor(95, 80) == QColor("white")
    canvas.close()


@pytest.mark.parametrize("operation_type", ["triangle", "text", ""])
def test_drawing_operation_rejects_non_stroke_types(operation_type: str) -> None:
    with pytest.raises(ValueError):
        DrawingOperation(operation_type, [(0, 0)], "#000000", 2)


@pytest.mark.parametrize("tool", ["line", "rectangle", "ellipse"])
def test_shape_tools_create_and_render_two_point_operations(app, tool) -> None:
    canvas = DrawingCanvas()
    canvas.resize(140, 110)
    canvas.set_pen_color("#3867d6")
    canvas.set_tool(tool)
    canvas.show()
    app.processEvents()

    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    QTest.mouseMove(canvas, QPoint(100, 80), delay=10)
    assert canvas.engine.current_operation is not None
    assert len(canvas.engine.current_operation.points) == 2
    preview = canvas.grab().toImage()
    assert any(
        preview.pixelColor(x, y) == QColor("#3867d6")
        for x in range(19, 102)
        for y in range(19, 82)
    )
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 80))
    app.processEvents()

    operation = canvas.engine.operations[0]
    assert operation.operation_type == tool
    assert operation.points == [(20.0, 20.0), (100.0, 80.0)]
    image = canvas.grab().toImage()
    assert any(
        image.pixelColor(x, y) == QColor("#3867d6")
        for x in range(19, 102)
        for y in range(19, 82)
    )
    canvas.close()


def test_png_export_has_canvas_dimensions_and_only_completed_board(tmp_path, app) -> None:
    canvas = DrawingCanvas()
    canvas.setMinimumSize(0, 0)
    canvas.resize(173, 119)
    canvas.set_tool("rectangle")
    canvas.engine.begin_stroke((20, 20), "#3867d6", 4, "rectangle")
    canvas.engine.update_shape((80, 70))
    canvas.engine.finish_stroke()
    canvas.set_remote_cursor(
        {"participant_id": "p", "username": "Remote", "color": "#e4572e", "x": 0.4, "y": 0.4}
    )
    canvas.engine.begin_stroke((105, 20), "#e4572e", 4, "line")
    canvas.engine.update_shape((155, 80))
    target = tmp_path / "board.png"

    assert canvas.export_png(str(target))
    image = QImage(str(target))
    assert image.width() > 80
    assert image.height() > 70
    assert image.pixelColor(0, 0) == QColor("white")
    assert image.pixelColor(50, 50) == QColor("white")  # remote cursor is not exported
    assert image.pixelColor(image.width() - 10, 50) == QColor("white")  # incomplete preview is not exported
    assert any(
        image.pixelColor(x, y) != QColor("white")
        for x in range(image.width()) for y in range(image.height())
    )
    canvas.close()
