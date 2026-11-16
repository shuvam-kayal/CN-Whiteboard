"""Validation helpers for the shared logical drawing-operation format."""

import math
import re
from typing import Any


MAX_STROKE_POINTS = 20_000
MAX_BRUSH_SIZE = 128.0
MAX_COORDINATE = 10_000_000.0
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_BRUSH_TYPES = {"pen", "pencil", "marker", "highlighter"}


class DrawingDataError(ValueError):
    """A drawing operation does not match the supported stroke format."""


def validate_drawing_operation(
    payload: Any, *, allow_sequence_number: bool = False
) -> dict[str, Any]:
    """Validate one stroke or two-point shape operation received over the wire."""
    required = {"operation_type", "points", "color", "brush_size"}
    allowed = required | {"brush_type", "opacity"} | ({"sequence_number"} if allow_sequence_number else set())
    legacy_allowed = required | ({"sequence_number"} if allow_sequence_number else set())
    if not isinstance(payload, dict) or set(payload) not in (allowed, legacy_allowed):
        raise DrawingDataError("drawing operation has missing or unexpected fields")

    operation_type = payload["operation_type"]
    operation_types = {"pen", "eraser", "line", "rectangle", "ellipse"}
    if not isinstance(operation_type, str) or operation_type not in operation_types:
        raise DrawingDataError("operation_type is not a supported drawing tool")
    color = payload["color"]
    if not isinstance(color, str) or not _HEX_COLOR.fullmatch(color):
        raise DrawingDataError("color must be a six-digit hexadecimal color")
    brush_size = payload["brush_size"]
    if (
        isinstance(brush_size, bool)
        or not isinstance(brush_size, (int, float))
        or not 0 < brush_size <= MAX_BRUSH_SIZE
        or not math.isfinite(brush_size)
    ):
        raise DrawingDataError(f"brush_size must be greater than 0 and at most {MAX_BRUSH_SIZE:g}")

    brush_type = payload.get("brush_type", "pen")
    if not isinstance(brush_type, str) or brush_type not in _BRUSH_TYPES:
        raise DrawingDataError("brush_type is not a supported brush")
    opacity = payload.get("opacity", 1.0)
    if (
        isinstance(opacity, bool)
        or not isinstance(opacity, (int, float))
        or not math.isfinite(opacity)
        or not 0 < opacity <= 1
    ):
        raise DrawingDataError("opacity must be greater than 0 and at most 1")

    points = payload["points"]
    if not isinstance(points, list) or not points:
        raise DrawingDataError("points must be a non-empty list")
    if len(points) > MAX_STROKE_POINTS:
        raise DrawingDataError(f"a stroke may contain at most {MAX_STROKE_POINTS} points")
    if operation_type in {"line", "rectangle", "ellipse"} and len(points) != 2:
        raise DrawingDataError("shape operations must contain exactly two endpoints")

    clean_points: list[list[float | int]] = []
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise DrawingDataError("each point must be a two-item coordinate list")
        coordinates = []
        for value in point:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or abs(value) > MAX_COORDINATE
                or not math.isfinite(value)
            ):
                raise DrawingDataError("point coordinates must be finite numeric values")
            coordinates.append(value)
        clean_points.append(coordinates)

    operation: dict[str, Any] = {
        "operation_type": operation_type,
        "points": clean_points,
        "color": color,
        "brush_size": brush_size,
        "brush_type": brush_type,
        "opacity": float(opacity),
    }
    if allow_sequence_number:
        sequence = payload["sequence_number"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise DrawingDataError("sequence_number must be a positive integer")
        operation["sequence_number"] = sequence
    return operation
