"""Atomic JSON storage for authoritative room drawing state."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from shared.app_paths import boards_directory as default_boards_directory
from shared.drawing import DrawingDataError, validate_drawing_operation


BOARD_FORMAT_VERSION = 3
MAX_SAVED_OPERATIONS = 50_000
MAX_BOARD_FILE_SIZE = 32 * 1024 * 1024
_ROOM_CODE = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")
_OPERATION_FIELDS = {
    "operation_type",
    "points",
    "color",
    "brush_size",
    "brush_type",
    "opacity",
    "sequence_number",
    "participant_id",
    "room_code",
    "active",
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
    undo_stacks: dict[str, list[int]]
    redo_stacks: dict[str, list[int]]
    participant_names: dict[str, str]


class PersistenceManager:
    """Read and atomically write room state under a trusted boards directory."""

    def __init__(self, boards_directory: str | Path | None = None) -> None:
        self.boards_directory = Path(
            boards_directory if boards_directory is not None else default_boards_directory()
        ).resolve()

    def save_board(
        self,
        room_code: str,
        operations: list[dict],
        undo_stacks: dict[str, list[int]] | None = None,
        redo_stacks: dict[str, list[int]] | None = None,
        participant_names: dict[str, str] | None = None,
    ) -> SavedBoard:
        code = self._validate_room_code(room_code)
        validated = self._validate_operations(code, operations)
        if undo_stacks is None:
            undo_stacks = self._default_undo_stacks(validated)
        clean_undo, clean_redo = self._validate_stacks(
            validated, undo_stacks, redo_stacks or {}
        )
        if participant_names is None:
            participant_names = {
                operation["participant_id"]: operation["participant_id"]
                for operation in validated
            }
        clean_names = self._validate_participant_names(participant_names)
        operation_owners = {operation["participant_id"] for operation in validated}
        if not operation_owners.issubset(clean_names):
            raise PersistenceError("invalid_board", "drawing participant metadata is incomplete")
        saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        document = {
            "format_version": BOARD_FORMAT_VERSION,
            "room_code": code,
            "saved_at": saved_at,
            "operations": validated,
            "undo_stacks": clean_undo,
            "redo_stacks": clean_redo,
            "participant_names": clean_names,
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

        return SavedBoard(code, saved_at, validated, clean_undo, clean_redo, clean_names)

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

        if not isinstance(document, dict):
            raise PersistenceError("invalid_board", "saved board has an invalid structure")
        version = document.get("format_version")
        legacy_fields = {"format_version", "room_code", "saved_at", "operations"}
        version_two_fields = legacy_fields | {"undo_stacks", "redo_stacks"}
        current_fields = version_two_fields | {"participant_names"}
        if isinstance(version, bool) or not isinstance(version, int):
            raise PersistenceError("invalid_board", "saved board version is unsupported")
        valid_fields = {
            1: legacy_fields,
            2: version_two_fields,
            BOARD_FORMAT_VERSION: current_fields,
        }
        if version not in valid_fields or set(document) != valid_fields[version]:
            raise PersistenceError("invalid_board", "saved board has an invalid structure")
        if document["format_version"] == 1:
            if not isinstance(document.get("operations"), list):
                raise PersistenceError("invalid_board", "saved operations must be a list")
            legacy_operations = []
            for operation in document["operations"]:
                if not isinstance(operation, dict):
                    raise PersistenceError("invalid_board", "saved operation is invalid")
                legacy_operations.append({**operation, "active": True})
            operations = self._validate_operations(code, legacy_operations)
            undo_stacks = self._default_undo_stacks(operations)
            redo_stacks: dict[str, list[int]] = {}
        else:
            operations = self._validate_operations(code, document["operations"])
            undo_stacks, redo_stacks = self._validate_stacks(
                operations, document["undo_stacks"], document["redo_stacks"]
            )
        if document["format_version"] == BOARD_FORMAT_VERSION:
            participant_names = self._validate_participant_names(document["participant_names"])
            operation_owners = {operation["participant_id"] for operation in operations}
            if not operation_owners.issubset(participant_names):
                raise PersistenceError("invalid_board", "drawing participant metadata is incomplete")
        else:
            # Older board formats had no usernames; preserve their owners opaquely.
            participant_names = {
                operation["participant_id"]: operation["participant_id"]
                for operation in operations
            }
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
        return SavedBoard(code, saved_at, operations, undo_stacks, redo_stacks, participant_names)

    @staticmethod
    def _validate_participant_names(names: object) -> dict[str, str]:
        if not isinstance(names, dict):
            raise PersistenceError("invalid_board", "participant names must be an object")
        if any(
            not isinstance(participant_id, str)
            or not participant_id
            or not isinstance(username, str)
            or not username
            for participant_id, username in names.items()
        ):
            raise PersistenceError("invalid_board", "participant name metadata is invalid")
        return dict(names)

    @staticmethod
    def _default_undo_stacks(operations: list[dict[str, Any]]) -> dict[str, list[int]]:
        stacks: dict[str, list[int]] = {}
        for operation in operations:
            if operation["active"]:
                stacks.setdefault(operation["participant_id"], []).append(
                    operation["sequence_number"]
                )
        return stacks

    @staticmethod
    def _validate_stacks(
        operations: list[dict[str, Any]], undo_stacks: object, redo_stacks: object
    ) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        if not isinstance(undo_stacks, dict) or not isinstance(redo_stacks, dict):
            raise PersistenceError("invalid_board", "undo/redo stacks must be objects")
        by_sequence = {operation["sequence_number"]: operation for operation in operations}
        used: set[int] = set()
        checked: list[dict[str, list[int]]] = []
        for stacks, should_be_active in ((undo_stacks, True), (redo_stacks, False)):
            clean: dict[str, list[int]] = {}
            for participant_id, sequences in stacks.items():
                if not isinstance(participant_id, str) or not participant_id:
                    raise PersistenceError("invalid_board", "undo/redo participant is invalid")
                if not isinstance(sequences, list):
                    raise PersistenceError("invalid_board", "undo/redo stack must be a list")
                clean_sequences: list[int] = []
                for sequence in sequences:
                    if (
                        isinstance(sequence, bool)
                        or not isinstance(sequence, int)
                        or sequence not in by_sequence
                        or sequence in used
                    ):
                        raise PersistenceError("invalid_board", "undo/redo sequence is invalid")
                    operation = by_sequence[sequence]
                    if operation["participant_id"] != participant_id or operation["active"] is not should_be_active:
                        raise PersistenceError("invalid_board", "undo/redo stack does not match operation state")
                    used.add(sequence)
                    clean_sequences.append(sequence)
                clean[participant_id] = clean_sequences
            checked.append(clean)
        return checked[0], checked[1]

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
            accepted_fields = (
                _OPERATION_FIELDS,
                _OPERATION_FIELDS - {"active"},
                _OPERATION_FIELDS - {"brush_type", "opacity"},
                _OPERATION_FIELDS - {"active", "brush_type", "opacity"},
            )
            if not isinstance(operation, dict) or set(operation) not in accepted_fields:
                raise PersistenceError("invalid_board", "saved drawing operation has an invalid structure")
            if operation.get("room_code") != room_code:
                raise PersistenceError("invalid_board", "drawing operation belongs to another room")
            if not isinstance(operation.get("participant_id"), str) or not operation["participant_id"]:
                raise PersistenceError("invalid_board", "drawing operation participant is invalid")
            active = operation.get("active", True)
            if not isinstance(active, bool):
                raise PersistenceError("invalid_board", "drawing operation status is invalid")
            drawing_payload = {
                "operation_type": operation["operation_type"],
                "points": operation["points"],
                "color": operation["color"],
                "brush_size": operation["brush_size"],
                "sequence_number": operation["sequence_number"],
            }
            if "brush_type" in operation or "opacity" in operation:
                drawing_payload["brush_type"] = operation.get("brush_type")
                drawing_payload["opacity"] = operation.get("opacity")
            try:
                drawing = validate_drawing_operation(
                    drawing_payload,
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
                    "active": active,
                }
            )
        return checked
