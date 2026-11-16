"""Local, GUI-independent storage for completed drawing operations."""

from dataclasses import dataclass
import math

from shared.drawing import DrawingDataError, validate_drawing_operation


Point = tuple[float, float]
Bounds = tuple[float, float, float, float]
SPATIAL_CELL_SIZE = 512.0
MAX_SPATIAL_CELLS_PER_OPERATION = 128
MAX_SPATIAL_CELLS_PER_QUERY = 10_000
_BRUSH_SCALE = {"pen": 1.0, "pencil": 0.72, "marker": 1.45, "highlighter": 2.4}


@dataclass
class DrawingOperation:
    """One ordered stroke or shape in canvas coordinates."""

    operation_type: str
    points: list[Point]
    color: str
    brush_size: float
    sequence_number: int | None = None
    operation_id: str | None = None
    active: bool = True
    brush_type: str = "pen"
    opacity: float = 1.0

    def __post_init__(self) -> None:
        if self.operation_type not in {"pen", "eraser", "line", "rectangle", "ellipse"}:
            raise ValueError("operation_type is not a supported drawing tool")
        if self.brush_type not in {"pen", "pencil", "marker", "highlighter"}:
            raise ValueError("brush_type is not a supported brush")
        if not isinstance(self.color, str) or not self.color:
            raise ValueError("color must be a non-empty string")
        if not isinstance(self.brush_size, (int, float)) or not math.isfinite(
            self.brush_size
        ) or self.brush_size <= 0:
            raise ValueError("brush_size must be a positive finite number")
        self.brush_size = float(self.brush_size)
        if (
            isinstance(self.opacity, bool)
            or not isinstance(self.opacity, (int, float))
            or not math.isfinite(self.opacity)
            or not 0 < self.opacity <= 1
        ):
            raise ValueError("opacity must be greater than 0 and at most 1")
        self.opacity = float(self.opacity)
        if not isinstance(self.active, bool):
            raise ValueError("active status must be a boolean")
        self.points = [self._point(point) for point in self.points]
        if self.operation_type in {"line", "rectangle", "ellipse"} and len(self.points) != 2:
            raise ValueError("shape operations require two endpoints")
        if self.sequence_number is not None and self.sequence_number < 1:
            raise ValueError("sequence_number must be positive")

    def to_payload(self) -> dict:
        """Return only the drawing data sent to the server."""
        return {
            "operation_type": self.operation_type,
            "points": [[x, y] for x, y in self.points],
            "color": self.color,
            "brush_size": self.brush_size,
            "brush_type": self.brush_type,
            "opacity": self.opacity,
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
        self._spatial_cells: dict[tuple[int, int], set[int]] = {}
        self._spatial_bounds: dict[int, Bounds] = {}
        self._spatial_operations: dict[int, DrawingOperation] = {}
        self._spatial_large: set[int] = set()
        self._render_positions: dict[int, int] = {}

    @property
    def current_operation(self) -> DrawingOperation | None:
        return self._current

    def begin_stroke(
        self,
        point: Point,
        color: str,
        brush_size: float,
        operation_type: str = "pen",
        brush_type: str = "pen",
        opacity: float = 1.0,
    ) -> None:
        if self._current is not None:
            raise RuntimeError("a stroke is already in progress")
        self._current = DrawingOperation(
            operation_type,
            [DrawingOperation._point(point)] * (2 if operation_type in {"line", "rectangle", "ellipse"} else 1),
            color,
            brush_size,
            brush_type=brush_type,
            opacity=opacity,
        )

    def add_point(self, point: Point) -> None:
        if self._current is None:
            raise RuntimeError("no stroke is in progress")
        self._current.points.append(DrawingOperation._point(point))

    def update_shape(self, point: Point) -> None:
        if self._current is None or self._current.operation_type not in {
            "line", "rectangle", "ellipse"
        }:
            raise RuntimeError("no shape is in progress")
        self._current.points[1] = DrawingOperation._point(point)

    def finish_stroke(self, point: Point | None = None) -> DrawingOperation | None:
        if self._current is None:
            return None
        if point is not None:
            if self._current.operation_type in {"line", "rectangle", "ellipse"}:
                self.update_shape(point)
            else:
                self.add_point(point)
        operation = self._current
        self.operations.append(operation)
        self._index_operation(operation)
        self._current = None
        return operation

    def add_remote_operation(self, payload: object) -> bool:
        """Store one validated server operation, ignoring duplicate sequences."""
        if not isinstance(payload, dict):
            raise ValueError("drawing operation must be an object")
        fields = ("operation_type", "points", "color", "brush_size", "sequence_number")
        data_payload = {field: payload.get(field) for field in fields}
        if "brush_type" in payload or "opacity" in payload:
            data_payload["brush_type"] = payload.get("brush_type")
            data_payload["opacity"] = payload.get("opacity")
        try:
            data = validate_drawing_operation(
                data_payload,
                allow_sequence_number=True,
            )
        except DrawingDataError as exc:
            raise ValueError(str(exc)) from exc

        sequence = data["sequence_number"]
        active = payload.get("active", True)
        if not isinstance(active, bool):
            raise ValueError("drawing operation active status must be a boolean")
        if sequence in self._remote_sequences:
            return False
        self._remote_sequences.add(sequence)
        previous = self.operations[-1] if self.operations else None
        self.operations.append(
            (operation := DrawingOperation(
                data["operation_type"],
                [(point[0], point[1]) for point in data["points"]],
                data["color"],
                data["brush_size"],
                sequence_number=sequence,
                active=active,
                brush_type=data["brush_type"],
                opacity=data["opacity"],
            ))
        )
        self._index_operation(operation)
        if previous is not None and (
            previous.sequence_number is None or previous.sequence_number >= sequence
        ):
            self._sort_confirmed_operations()
        return True

    def set_operation_active(self, sequence_number: int, active: bool) -> bool:
        """Apply an authoritative undo/redo status to one confirmed stroke."""
        if not isinstance(active, bool):
            raise ValueError("operation active status must be a boolean")
        for operation in self.operations:
            if operation.sequence_number == sequence_number:
                operation.active = active
                return True
        return False

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
        self._spatial_cells = replacement._spatial_cells
        self._spatial_bounds = replacement._spatial_bounds
        self._spatial_operations = replacement._spatial_operations
        self._spatial_large = replacement._spatial_large
        self._render_positions = replacement._render_positions
        self._current = None

    def _sort_confirmed_operations(self) -> None:
        self.operations.sort(
            key=lambda operation: (
                operation.sequence_number is None,
                operation.sequence_number or 0,
            )
        )
        self._render_positions = {
            id(operation): position for position, operation in enumerate(self.operations)
        }

    @staticmethod
    def operation_bounds(operation: DrawingOperation) -> Bounds:
        """Return world-space bounds including the brush's widest rendered edge."""
        if not operation.points:
            return 0.0, 0.0, 0.0, 0.0
        scale = _BRUSH_SCALE.get(operation.brush_type, 1.0)
        margin = operation.brush_size * scale / 2 + 2.0
        xs = [point[0] for point in operation.points]
        ys = [point[1] for point in operation.points]
        return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin

    @classmethod
    def operations_bounds(cls, operations: list[DrawingOperation]) -> Bounds | None:
        """Union the visual bounds of a set of operations."""
        if not operations:
            return None
        bounds = [cls.operation_bounds(operation) for operation in operations]
        return (
            min(item[0] for item in bounds),
            min(item[1] for item in bounds),
            max(item[2] for item in bounds),
            max(item[3] for item in bounds),
        )

    def visible_operations(self, bounds: Bounds) -> list[DrawingOperation]:
        """Return operations overlapping a world-space viewport in board order."""
        left, top, right, bottom = bounds
        min_x = math.floor(left / SPATIAL_CELL_SIZE)
        max_x = math.floor(right / SPATIAL_CELL_SIZE)
        min_y = math.floor(top / SPATIAL_CELL_SIZE)
        max_y = math.floor(bottom / SPATIAL_CELL_SIZE)
        cell_count = (max_x - min_x + 1) * (max_y - min_y + 1)
        if cell_count > MAX_SPATIAL_CELLS_PER_QUERY:
            candidates = set(self._spatial_operations)
        else:
            candidates = set(self._spatial_large)
            for cell_x in range(min_x, max_x + 1):
                for cell_y in range(min_y, max_y + 1):
                    candidates.update(self._spatial_cells.get((cell_x, cell_y), ()))
        visible = [
            key for key in candidates
            if key in self._spatial_bounds
            and self._intersects(self._spatial_bounds[key], bounds)
        ]
        visible.sort(key=lambda key: self._render_positions.get(key, 0))
        return [self._spatial_operations[key] for key in visible]

    def _index_operation(self, operation: DrawingOperation) -> None:
        key = id(operation)
        bounds = self.operation_bounds(operation)
        self._spatial_bounds[key] = bounds
        self._spatial_operations[key] = operation
        self._render_positions[key] = len(self.operations) - 1
        min_x, min_y, max_x, max_y = (
            math.floor(bounds[0] / SPATIAL_CELL_SIZE),
            math.floor(bounds[1] / SPATIAL_CELL_SIZE),
            math.floor(bounds[2] / SPATIAL_CELL_SIZE),
            math.floor(bounds[3] / SPATIAL_CELL_SIZE),
        )
        cell_count = (max_x - min_x + 1) * (max_y - min_y + 1)
        if cell_count > MAX_SPATIAL_CELLS_PER_OPERATION:
            self._spatial_large.add(key)
            return
        for cell_x in range(min_x, max_x + 1):
            for cell_y in range(min_y, max_y + 1):
                self._spatial_cells.setdefault((cell_x, cell_y), set()).add(key)

    @staticmethod
    def _intersects(first: Bounds, second: Bounds) -> bool:
        return not (
            first[2] < second[0] or first[0] > second[2]
            or first[3] < second[1] or first[1] > second[3]
        )

    def clear(self) -> None:
        self.operations.clear()
        self._current = None
        self._remote_sequences.clear()
        self._spatial_cells.clear()
        self._spatial_bounds.clear()
        self._spatial_operations.clear()
        self._spatial_large.clear()
        self._render_positions.clear()
