"""Atomic JSON storage for authoritative room drawing state."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from shared.drawing import DrawingDataError, validate_drawing_operation


BOARD_FORMAT_VERSION = 1
MAX_SAVED_OPERATIONS = 50_000
MAX_BOARD_FILE_SIZE = 32 * 1024 * 1024
_ROOM_CODE = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")
_OPERATION_FIELDS = {
    "operation_type",
    "points",
    "color",
    "brush_size",
    "sequence_number",
    "participant_id",
    "room_code",
}


class PersistenceError(ValueError):
    """A board save could not be written or is not valid for loading."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class SavedBoard:
    room_code: str
    saved_at: str
    operations: list[dict[str, Any]]


class PersistenceManager:
    """Read and atomically write room state under a trusted boards directory."""

    def __init__(self, boards_directory: str | Path | None = None) -> None:
        default_directory = Path(__file__).resolve().parents[1] / "data" / "boards"
        self.boards_directory = Path(boards_directory or default_directory).resolve()

    def save_board(self, room_code: str, operations: list[dict]) -> SavedBoard:
        code = self._validate_room_code(room_code)
        validated = self._validate_operations(code, operations)
        saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        document = {
            "format_version": BOARD_FORMAT_VERSION,
            "room_code": code,
            "saved_at": saved_at,
            "operations": validated,
        }
        encoded = json.dumps(
            document, ensure_ascii=False, indent=2, allow_nan=False
        ).encode("utf-8")
        if len(encoded) > MAX_BOARD_FILE_SIZE:
            raise PersistenceError("board_too_large", "board save exceeds the file size limit")

        destination = self.boards_directory / f"{code}.json"
        temporary_path: str | None = None
        try:
            self.boards_directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{code}.",
                suffix=".tmp",
                dir=self.boards_directory,
                delete=False,
            ) as temporary_file:
                temporary_path = temporary_file.name
                temporary_file.write(encoded)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
        except OSError as exc:
            raise PersistenceError("save_failed", f"could not save board: {exc}") from exc
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

        return SavedBoard(code, saved_at, validated)

    def load_board(self, room_code: str) -> SavedBoard:
        code = self._validate_room_code(room_code)
        path = self.boards_directory / f"{code}.json"
        try:
            if path.stat().st_size > MAX_BOARD_FILE_SIZE:
                raise PersistenceError("invalid_board", "board save exceeds the file size limit")
            with path.open("r", encoding="utf-8") as stream:
                document = json.load(stream)
        except FileNotFoundError as exc:
            raise PersistenceError("board_not_found", "no saved board exists for this room") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PersistenceError("invalid_board", f"could not read board save: {exc}") from exc

        if not isinstance(document, dict) or set(document) != {
            "format_version",
            "room_code",
            "saved_at",
            "operations",
        }:
            raise PersistenceError("invalid_board", "saved board has an invalid structure")
        version = document["format_version"]
        if isinstance(version, bool) or version != BOARD_FORMAT_VERSION:
            raise PersistenceError("invalid_board", "saved board version is unsupported")
        if document["room_code"] != code:
            raise PersistenceError("invalid_board", "saved board room code does not match")
        saved_at = document["saved_at"]
        if not isinstance(saved_at, str):
            raise PersistenceError("invalid_board", "saved board timestamp is invalid")
        try:
            timestamp = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PersistenceError("invalid_board", "saved board timestamp is invalid") from exc
        if timestamp.tzinfo is None:
            raise PersistenceError("invalid_board", "saved board timestamp must include a timezone")
        operations = self._validate_operations(code, document["operations"])
        return SavedBoard(code, saved_at, operations)

    @staticmethod
    def _validate_room_code(room_code: str) -> str:
        if not isinstance(room_code, str) or not _ROOM_CODE.fullmatch(room_code):
            raise PersistenceError("invalid_room_code", "room code is not safe for a board filename")
        return room_code

    @staticmethod
    def _validate_operations(room_code: str, operations: object) -> list[dict[str, Any]]:
        if not isinstance(operations, list) or len(operations) > MAX_SAVED_OPERATIONS:
            raise PersistenceError("invalid_board", "saved operations must be a valid bounded list")

        checked: list[dict[str, Any]] = []
        for expected_sequence, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict) or set(operation) != _OPERATION_FIELDS:
                raise PersistenceError("invalid_board", "saved drawing operation has an invalid structure")
            if operation.get("room_code") != room_code:
                raise PersistenceError("invalid_board", "drawing operation belongs to another room")
            if not isinstance(operation.get("participant_id"), str) or not operation["participant_id"]:
                raise PersistenceError("invalid_board", "drawing operation participant is invalid")
            try:
                drawing = validate_drawing_operation(
                    {
                        "operation_type": operation["operation_type"],
                        "points": operation["points"],
                        "color": operation["color"],
                        "brush_size": operation["brush_size"],
                        "sequence_number": operation["sequence_number"],
                    },
                    allow_sequence_number=True,
                )
            except DrawingDataError as exc:
                raise PersistenceError("invalid_board", str(exc)) from exc
            if drawing["sequence_number"] != expected_sequence:
                raise PersistenceError("invalid_board", "drawing operation sequence is invalid")
            checked.append(
                {
                    **drawing,
                    "participant_id": operation["participant_id"],
                    "room_code": room_code,
                }
            )
        return checked
