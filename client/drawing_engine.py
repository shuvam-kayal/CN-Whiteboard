"""Local, GUI-independent storage for completed drawing operations."""

from dataclasses import dataclass
import math

from shared.drawing import DrawingDataError, validate_drawing_operation


Point = tuple[float, float]


@dataclass
class DrawingOperation:
    """One ordered pen or eraser stroke in canvas coordinates."""

    operation_type: str
    points: list[Point]
    color: str
    brush_size: float
    sequence_number: int | None = None
    operation_id: str | None = None

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
        if self.sequence_number is not None and self.sequence_number < 1:
            raise ValueError("sequence_number must be positive")

    def to_payload(self) -> dict:
        """Return only the drawing data sent to the server."""
        return {
            "operation_type": self.operation_type,
            "points": [[x, y] for x, y in self.points],
            "color": self.color,
            "brush_size": self.brush_size,
        }

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
        self._remote_sequences: set[int] = set()

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

    def add_remote_operation(self, payload: object) -> bool:
        """Store one validated server operation, ignoring duplicate sequences."""
        if not isinstance(payload, dict):
            raise ValueError("drawing operation must be an object")
        fields = ("operation_type", "points", "color", "brush_size", "sequence_number")
        try:
            data = validate_drawing_operation(
                {field: payload.get(field) for field in fields},
                allow_sequence_number=True,
            )
        except DrawingDataError as exc:
            raise ValueError(str(exc)) from exc

        sequence = data["sequence_number"]
        if sequence in self._remote_sequences:
            return False
        self._remote_sequences.add(sequence)
        self.operations.append(
            DrawingOperation(
                data["operation_type"],
                [(point[0], point[1]) for point in data["points"]],
                data["color"],
                data["brush_size"],
                sequence_number=sequence,
            )
        )
        self._sort_confirmed_operations()
        return True

    def confirm_local_operation(self, operation_id: str, sequence_number: int) -> bool:
        """Attach the server sequence to an already rendered local operation."""
        if (
            not isinstance(sequence_number, int)
            or isinstance(sequence_number, bool)
            or sequence_number < 1
        ):
            raise ValueError("sequence_number must be a positive integer")
        for operation in self.operations:
            if operation.operation_id == operation_id:
                operation.sequence_number = sequence_number
                self._sort_confirmed_operations()
                return True
        return False

    def replace_authoritative_state(self, operations: object) -> None:
        """Replace the canvas history with a validated server snapshot."""
        if not isinstance(operations, list):
            raise ValueError("board state operations must be a list")
        replacement = DrawingEngine()
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("board state operation must be an object")
            replacement.add_remote_operation(operation)
        if len(replacement.operations) != len(operations):
            raise ValueError("board state contains duplicate operation sequences")
        sequences = [operation.sequence_number for operation in replacement.operations]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("board state operation sequence is incomplete")
        self.operations = replacement.operations
        self._remote_sequences = replacement._remote_sequences
        self._current = None

    def _sort_confirmed_operations(self) -> None:
        self.operations.sort(
            key=lambda operation: (
                operation.sequence_number is None,
                operation.sequence_number or 0,
            )
        )

    def clear(self) -> None:
        self.operations.clear()
        self._current = None
        self._remote_sequences.clear()
