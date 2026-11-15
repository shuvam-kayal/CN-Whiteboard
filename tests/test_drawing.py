import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
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


@pytest.mark.parametrize("operation_type", ["shape", "text", ""])
def test_drawing_operation_rejects_non_stroke_types(operation_type: str) -> None:
    with pytest.raises(ValueError):
        DrawingOperation(operation_type, [(0, 0)], "#000000", 2)
