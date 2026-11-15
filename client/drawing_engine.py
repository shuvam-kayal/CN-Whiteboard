"""Local, GUI-independent storage for completed drawing operations."""

from dataclasses import dataclass
import math


Point = tuple[float, float]


@dataclass
class DrawingOperation:
    """One ordered pen or eraser stroke in canvas coordinates."""

    operation_type: str
    points: list[Point]
    color: str
    brush_size: float

    def __post_init__(self) -> None:
        if self.operation_type not in {"pen", "eraser"}:
            raise ValueError("operation_type must be 'pen' or 'eraser'")
        if not isinstance(self.color, str) or not self.color:
            raise ValueError("color must be a non-empty string")
        if not isinstance(self.brush_size, (int, float)) or not math.isfinite(
            self.brush_size
        ) or self.brush_size <= 0:
            raise ValueError("brush_size must be a positive finite number")
        self.brush_size = float(self.brush_size)
        self.points = [self._point(point) for point in self.points]

    @staticmethod
    def _point(point: Point) -> Point:
        if len(point) != 2:
            raise ValueError("each point must contain x and y coordinates")
        x, y = point
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("coordinates must be finite numbers")
        return float(x), float(y)


class DrawingEngine:
    """Record completed strokes while keeping an in-progress stroke separate."""

    def __init__(self) -> None:
        self.operations: list[DrawingOperation] = []
        self._current: DrawingOperation | None = None

    @property
    def current_operation(self) -> DrawingOperation | None:
        return self._current

    def begin_stroke(
        self,
        point: Point,
        color: str,
        brush_size: float,
        operation_type: str = "pen",
    ) -> None:
        if self._current is not None:
            raise RuntimeError("a stroke is already in progress")
        self._current = DrawingOperation(
            operation_type, [DrawingOperation._point(point)], color, brush_size
        )

    def add_point(self, point: Point) -> None:
        if self._current is None:
            raise RuntimeError("no stroke is in progress")
        self._current.points.append(DrawingOperation._point(point))

    def finish_stroke(self, point: Point | None = None) -> DrawingOperation | None:
        if self._current is None:
            return None
        if point is not None:
            self.add_point(point)
        operation = self._current
        self.operations.append(operation)
        self._current = None
        return operation

    def clear(self) -> None:
        self.operations.clear()
        self._current = None
