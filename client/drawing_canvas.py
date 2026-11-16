"""PySide6 canvas that renders local drawing operations and mouse strokes."""

import math
import uuid

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget

from client.drawing_engine import DrawingEngine, DrawingOperation
from shared.drawing import MAX_BRUSH_SIZE


class DrawingCanvas(QWidget):
    """Mouse-driven canvas backed by a replayable local operation list."""

    operation_completed = Signal(str, dict)
    cursor_moved = Signal(float, float)
    view_changed = Signal(float)
    MIN_ZOOM = 0.25
    MAX_ZOOM = 4.0
    ZOOM_STEP = 1.2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("drawingCanvas")
        self.setMinimumSize(420, 420)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.engine = DrawingEngine()
        self.remote_cursors: dict[str, dict] = {}
        self.tool = "pen"
        self.pen_color = "#202943"
        self.brush_size = 4.0
        self.brush_type = "pen"
        self.brush_opacity = 1.0
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._panning = False
        self._last_pan_position: QPointF | None = None

    def set_tool(self, tool: str) -> None:
        if tool not in {"pen", "eraser", "line", "rectangle", "ellipse"}:
            raise ValueError("tool is not supported")
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

    def set_brush_type(self, brush_type: str) -> None:
        settings = {"pen": 1.0, "pencil": 0.62, "marker": 0.72, "highlighter": 0.28}
        if brush_type not in settings:
            raise ValueError("brush type is not supported")
        self.brush_type = brush_type
        self.brush_opacity = settings[brush_type]

    def screen_to_world(self, position: QPointF) -> QPointF:
        """Convert viewport-local screen coordinates to stable drawing coordinates."""
        return QPointF(position.x() / self.zoom + self.pan_x,
                       position.y() / self.zoom + self.pan_y)

    def world_to_screen(self, position: QPointF) -> QPointF:
        """Convert stable drawing coordinates to viewport-local screen coordinates."""
        return QPointF((position.x() - self.pan_x) * self.zoom,
                       (position.y() - self.pan_y) * self.zoom)

    def zoom_at(self, factor: float, screen_position: QPointF | None = None) -> None:
        """Change local zoom while keeping the world point under screen_position fixed."""
        if screen_position is None:
            screen_position = QPointF(self.width() / 2, self.height() / 2)
        world_anchor = self.screen_to_world(screen_position)
        new_zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, self.zoom * factor))
        if math.isclose(new_zoom, self.zoom):
            return
        self.zoom = new_zoom
        self.pan_x = world_anchor.x() - screen_position.x() / new_zoom
        self.pan_y = world_anchor.y() - screen_position.y() / new_zoom
        self.view_changed.emit(self.zoom)
        self.update()

    def zoom_in(self) -> None:
        self.zoom_at(self.ZOOM_STEP)

    def zoom_out(self) -> None:
        self.zoom_at(1.0 / self.ZOOM_STEP)

    def reset_view(self) -> None:
        if self.zoom == 1.0 and self.pan_x == 0.0 and self.pan_y == 0.0:
            return
        self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
        self.view_changed.emit(self.zoom)
        self.update()

    def clear(self) -> None:
        self.engine.clear()
        self.update()

    def add_remote_operation(self, payload: object) -> bool:
        """Apply a server operation on the GUI thread and repaint the canvas."""
        added = self.engine.add_remote_operation(payload)
        if added:
            self.update()
        return added

    def replace_board_state(self, operations: object) -> None:
        """Apply a complete authoritative snapshot and repaint once."""
        self.engine.replace_authoritative_state(operations)
        self.update()

    def set_operation_active(self, sequence_number: int, active: bool) -> bool:
        changed = self.engine.set_operation_active(sequence_number, active)
        if changed:
            self.update()
        return changed

    def export_png(self, path: str) -> bool:
        """Write only the completed whiteboard at the widget's current dimensions."""
        if not path:
            return False
        image = QImage(self.size(), QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for operation in self.engine.operations:
            if operation.active:
                self._render_operation(painter, operation)
        painter.end()
        return image.save(path, "PNG")

    def confirm_local_operation(self, operation_id: str, sequence_number: int) -> None:
        if self.engine.confirm_local_operation(operation_id, sequence_number):
            self.update()

    def set_remote_cursor(self, cursor: object) -> None:
        if not isinstance(cursor, dict):
            return
        participant_id = cursor.get("participant_id")
        username = cursor.get("username")
        color = cursor.get("color")
        x, y = cursor.get("x"), cursor.get("y")
        if (
            not isinstance(participant_id, str)
            or not isinstance(username, str)
            or not isinstance(color, str)
            or not QColor(color).isValid()
            or isinstance(x, bool)
            or not isinstance(x, (int, float))
            or isinstance(y, bool)
            or not isinstance(y, (int, float))
            or not math.isfinite(x)
            or not math.isfinite(y)
            or not 0 <= x <= 1
            or not 0 <= y <= 1
        ):
            return
        self.remote_cursors[participant_id] = {
            "username": username,
            "color": color,
            "x": float(x),
            "y": float(y),
        }
        self.update()

    def remove_remote_cursors(self, participant_ids: set[str]) -> None:
        removed = self.remote_cursors.keys() - participant_ids
        if removed:
            for participant_id in removed:
                self.remote_cursors.pop(participant_id, None)
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_pan_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_cursor_position(event)
            operation_type = self.tool
            self.engine.begin_stroke(
                self._event_world_point(event),
                "#ffffff" if operation_type == "eraser" else self.pen_color,
                self.brush_size,
                operation_type,
                "pen" if operation_type == "eraser" else self.brush_type,
                1.0 if operation_type == "eraser" else self.brush_opacity,
            )
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._last_pan_position is not None:
            position = event.position()
            delta = position - self._last_pan_position
            self.pan_x -= delta.x() / self.zoom
            self.pan_y -= delta.y() / self.zoom
            self._last_pan_position = position
            self._emit_cursor_position(event)
            self.update()
            event.accept()
            return
        self._emit_cursor_position(event)
        if self.engine.current_operation is not None:
            if self.engine.current_operation.operation_type in {"line", "rectangle", "ellipse"}:
                self.engine.update_shape(self._event_world_point(event))
            else:
                self.engine.add_point(self._event_world_point(event))
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._last_pan_position = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_cursor_position(event)
            operation = self.engine.finish_stroke(self._event_world_point(event))
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
        painter.save()
        painter.setWorldTransform(
            QTransform(
                self.zoom, 0, 0, self.zoom,
                -self.pan_x * self.zoom, -self.pan_y * self.zoom,
            )
        )
        for operation in self.engine.operations:
            if operation.active:
                self._render_operation(painter, operation)
        if self.engine.current_operation is not None:
            self._render_operation(painter, self.engine.current_operation)
        painter.restore()
        for cursor in self.remote_cursors.values():
            self._render_cursor(painter, cursor)
        painter.end()

    @staticmethod
    def _event_point(event) -> tuple[float, float]:
        position = event.position()
        return position.x(), position.y()

    def _event_world_point(self, event) -> tuple[float, float]:
        point = self.screen_to_world(event.position())
        return point.x(), point.y()

    def _emit_cursor_position(self, event) -> None:
        x, y = self._event_world_point(event)
        self.cursor_moved.emit(
            min(1.0, max(0.0, x / max(1, self.width() - 1))),
            min(1.0, max(0.0, y / max(1, self.height() - 1))),
        )

    @staticmethod
    def _render_operation(painter: QPainter, operation: DrawingOperation) -> None:
        if not operation.points:
            return
        painter.save()
        painter.setOpacity(operation.opacity)
        brush_scale = {"pen": 1.0, "pencil": 0.72, "marker": 1.45, "highlighter": 2.4}.get(
            operation.brush_type, 1.0
        )
        pen = QPen(
            QColor(operation.color),
            max(1.0, operation.brush_size * brush_scale),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        if operation.operation_type == "line":
            painter.drawLine(
                QPointF(*operation.points[0]), QPointF(*operation.points[1])
            )
            painter.restore()
            return
        if operation.operation_type in {"rectangle", "ellipse"}:
            start, end = operation.points
            bounds = QRectF(QPointF(*start), QPointF(*end)).normalized()
            if operation.operation_type == "rectangle":
                painter.drawRect(bounds)
            else:
                painter.drawEllipse(bounds)
            painter.restore()
            return
        if len(operation.points) == 1:
            x, y = operation.points[0]
            painter.drawPoint(round(x), round(y))
            painter.restore()
            return
        for start, end in zip(operation.points, operation.points[1:]):
            painter.drawLine(round(start[0]), round(start[1]), round(end[0]), round(end[1]))
        painter.restore()

    def _render_cursor(self, painter: QPainter, cursor: dict) -> None:
        world_position = QPointF(
            cursor["x"] * max(1, self.width() - 1),
            cursor["y"] * max(1, self.height() - 1),
        )
        screen_position = self.world_to_screen(world_position)
        x, y = screen_position.x(), screen_position.y()
        color = QColor(cursor["color"])
        painter.save()
        painter.setPen(QPen(QColor("white"), 2))
        painter.setBrush(color)
        painter.drawEllipse(QPointF(x, y), 7, 7)

        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(cursor["username"])
        label_width = text_width + 14
        label_x = x + 11
        if label_x + label_width > self.width():
            label_x = x - label_width - 11
        label_x = max(1, min(self.width() - label_width - 1, label_x))
        label_y = max(1, min(self.height() - 23, y - 27))
        label_rect = QRectF(label_x, label_y, label_width, 21)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(label_rect, 5, 5)
        painter.setPen(QColor("white"))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, cursor["username"])
        painter.restore()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            factor = self.ZOOM_STEP if delta > 0 else 1.0 / self.ZOOM_STEP
            self.zoom_at(factor, event.position())
            event.accept()
            return
        super().wheelEvent(event)
