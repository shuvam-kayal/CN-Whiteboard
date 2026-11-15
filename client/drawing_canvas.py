"""PySide6 canvas that renders local drawing operations and mouse strokes."""

import math
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from client.drawing_engine import DrawingEngine, DrawingOperation
from shared.drawing import MAX_BRUSH_SIZE


class DrawingCanvas(QWidget):
    """Mouse-driven canvas backed by a replayable local operation list."""

    operation_completed = Signal(str, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("drawingCanvas")
        self.setMinimumSize(420, 420)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.engine = DrawingEngine()
        self.tool = "pen"
        self.pen_color = "#202943"
        self.brush_size = 4.0

    def set_tool(self, tool: str) -> None:
        if tool not in {"pen", "eraser"}:
            raise ValueError("tool must be 'pen' or 'eraser'")
        self.tool = tool

    def set_pen_color(self, color: str) -> None:
        parsed = QColor(color)
        if not parsed.isValid():
            raise ValueError("color must be a valid Qt color")
        self.pen_color = parsed.name(QColor.NameFormat.HexRgb)

    def set_brush_size(self, size: float) -> None:
        if not math.isfinite(size) or not 0 < size <= MAX_BRUSH_SIZE:
            raise ValueError(
                f"brush size must be positive and at most {MAX_BRUSH_SIZE:g}"
            )
        self.brush_size = float(size)

    def clear(self) -> None:
        self.engine.clear()
        self.update()

    def add_remote_operation(self, payload: object) -> bool:
        """Apply a server operation on the GUI thread and repaint the canvas."""
        added = self.engine.add_remote_operation(payload)
        if added:
            self.update()
        return added

    def confirm_local_operation(self, operation_id: str, sequence_number: int) -> None:
        if self.engine.confirm_local_operation(operation_id, sequence_number):
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.engine.begin_stroke(
                self._event_point(event),
                "#ffffff" if self.tool == "eraser" else self.pen_color,
                self.brush_size,
                self.tool,
            )
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.engine.current_operation is not None:
            self.engine.add_point(self._event_point(event))
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            operation = self.engine.finish_stroke(self._event_point(event))
            if operation is not None:
                operation.operation_id = uuid.uuid4().hex
                self.operation_completed.emit(
                    operation.operation_id, operation.to_payload()
                )
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("white"))
        for operation in self.engine.operations:
            self._render_operation(painter, operation)
        if self.engine.current_operation is not None:
            self._render_operation(painter, self.engine.current_operation)
        painter.end()

    @staticmethod
    def _event_point(event) -> tuple[float, float]:
        position = event.position()
        return position.x(), position.y()

    @staticmethod
    def _render_operation(painter: QPainter, operation: DrawingOperation) -> None:
        if not operation.points:
            return
        pen = QPen(
            QColor(operation.color),
            operation.brush_size,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        if len(operation.points) == 1:
            x, y = operation.points[0]
            painter.drawPoint(round(x), round(y))
            return
        for start, end in zip(operation.points, operation.points[1:]):
            painter.drawLine(round(start[0]), round(start[1]), round(end[0]), round(end[1]))
