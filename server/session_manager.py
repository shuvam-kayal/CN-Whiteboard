"""Thread-safe room and participant state, independent of socket handling."""

from dataclasses import dataclass, field
import colorsys
from datetime import datetime, timezone
import json
import math
import secrets
import threading
import time
import uuid

from shared.drawing import MAX_COORDINATE, DrawingDataError, validate_drawing_operation
from shared.protocol import MAX_MESSAGE_SIZE, ProtocolError, encode_message
from server.persistence import PersistenceError, PersistenceManager


ROOM_CODE_LENGTH = 6
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CURSOR_COLORS = (
    "#e4572e",
    "#3478f6",
    "#239b72",
    "#a855f7",
    "#d08b00",
    "#d946a8",
    "#0e8f9a",
    "#df3f4f",
    "#5c60d8",
    "#8a5a32",
)
MAX_CHAT_MESSAGE_LENGTH = 2_000
MAX_ROOM_OPERATIONS = 50_000


class SessionError(ValueError):
    """A room or participant request cannot be completed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Participant:
    participant_id: str
    username: str
    is_host: bool
    cursor_color: str
    resume_token: str

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "participant_id": self.participant_id,
            "username": self.username,
            "is_host": self.is_host,
            "cursor_color": self.cursor_color,
        }


@dataclass
class Room:
    code: str
    participants: dict[str, Participant]
    drawing_operations: list[dict]
    undo_stacks: dict[str, list[int]] = field(default_factory=dict)
    redo_stacks: dict[str, list[int]] = field(default_factory=dict)
    participant_names: dict[str, str] = field(default_factory=dict)
    disconnected_at: float | None = None
    resume_tokens: dict[str, str] = field(default_factory=dict)
    drawing_state_bytes: int = 0

    def participant_list(self) -> list[dict[str, str | bool]]:
        return [participant.as_dict() for participant in self.participants.values()]


@dataclass
class RoomChange:
    room_code: str
    participant: dict[str, str | bool] | None
    participants: list[dict[str, str | bool]]
    recipients: tuple[str, ...]
    resume_token: str | None = None
    replaced_client_id: str | None = None


@dataclass
class DrawingChange:
    operation: dict
    recipients: tuple[str, ...]


@dataclass
class CursorChange:
    cursor: dict
    recipients: tuple[str, ...]


@dataclass
class ChatChange:
    message: dict
    recipients: tuple[str, ...]


@dataclass
class BoardLoadChange:
    room_code: str
    saved_at: str
    operations: list[dict]
    recipients: tuple[str, ...]


@dataclass
class DrawingStatusChange:
    status: dict
    recipients: tuple[str, ...]


class SessionManager:
    """Manage independent rooms and map connected clients to memberships."""

    def __init__(
        self,
        persistence: PersistenceManager | None = None,
        reconnect_grace_period: float = 30.0,
    ) -> None:
        self._rooms: dict[str, Room] = {}
        self._client_rooms: dict[str, str] = {}
        self._lock = threading.RLock()
        self.persistence = persistence or PersistenceManager()
        self.reconnect_grace_period = max(0.0, reconnect_grace_period)

    @property
    def room_count(self) -> int:
        with self._lock:
            return sum(bool(room.participants) for room in self._rooms.values())

    def room_code_for_client(self, client_id: str) -> str | None:
        """Return the caller's room code without exposing mutable state."""
        with self._lock:
            return self._client_rooms.get(client_id)

    def prune_expired_rooms(self, now: float | None = None) -> int:
        """Drop empty rooms after their bounded reconnect window expires."""
        with self._lock:
            current = time.monotonic() if now is None else now
            expired = [
                code
                for code, room in self._rooms.items()
                if not room.participants
                and room.disconnected_at is not None
                and current - room.disconnected_at >= self.reconnect_grace_period
            ]
            for code in expired:
                del self._rooms[code]
            return len(expired)

    def create_room(self, client_id: str, username: str) -> RoomChange:
        with self._lock:
            self._ensure_unassigned(client_id)
            name = self._validate_username(username)
            code = self._new_room_code()
            participant = Participant(
                uuid.uuid4().hex, name, True, CURSOR_COLORS[0], secrets.token_urlsafe(32)
            )
            room = Room(
                code,
                {client_id: participant},
                [],
                participant_names={participant.participant_id: participant.username},
                resume_tokens={participant.resume_token: participant.participant_id},
            )
            room.drawing_state_bytes = self._board_state_size(code, [])
            self._rooms[code] = room
            self._client_rooms[client_id] = code
            return self._change(room, participant)

    def join_room(
        self,
        client_id: str,
        room_code: str,
        username: str,
        resume_token: str | None = None,
    ) -> RoomChange:
        with self._lock:
            self.prune_expired_rooms()
            self._ensure_unassigned(client_id)
            if resume_token is not None and (
                not isinstance(resume_token, str) or len(resume_token) > 128
            ):
                raise SessionError("invalid_resume_token", "resume token is invalid")
            if not isinstance(room_code, str) or len(room_code) != ROOM_CODE_LENGTH or any(
                character not in ROOM_CODE_ALPHABET for character in room_code.upper()
            ):
                raise SessionError("invalid_room_code", "room code has an invalid format")
            normalized_code = room_code.upper()
            room = self._rooms.get(normalized_code)
            if room is None:
                try:
                    saved = self.persistence.load_board(normalized_code)
                except PersistenceError as exc:
                    code = "room_not_found" if exc.code == "board_not_found" else "board_restore_failed"
                    message = "no room exists with that code" if code == "room_not_found" else str(exc)
                    raise SessionError(code, message) from exc
                room = Room(
                    normalized_code, {}, saved.operations,
                    saved.undo_stacks, saved.redo_stacks, saved.participant_names
                )
                try:
                    room.drawing_state_bytes = self._board_state_size(
                        normalized_code, saved.operations
                    )
                except SessionError as exc:
                    raise SessionError("board_too_large", str(exc)) from exc
                self._rooms[normalized_code] = room
            requested_name = self._validate_username(username)
            resume_id = room.resume_tokens.get(resume_token) if isinstance(resume_token, str) else None
            replaced_client_id = None
            resumed_participant = None
            if resume_id is not None:
                for old_client_id, current in tuple(room.participants.items()):
                    if current.participant_id == resume_id:
                        replaced_client_id = old_client_id
                        resumed_participant = current
                        room.participants.pop(old_client_id)
                        self._client_rooms.pop(old_client_id, None)
                        break
            if resumed_participant is not None:
                participant = resumed_participant
            elif resume_id is not None:
                previous_name = room.participant_names.get(resume_id, requested_name)
                name = self._unique_username(room, previous_name)
                participant = Participant(
                    resume_id,
                    name,
                    not room.participants,
                    self._next_cursor_color(room),
                    resume_token,
                )
            else:
                name = self._unique_username(room, requested_name)
                restored_id = next(
                    (
                        participant_id
                        for participant_id, previous_name in room.participant_names.items()
                        if previous_name.casefold() == name.casefold()
                        and all(
                            member.participant_id != participant_id
                            for member in room.participants.values()
                        )
                    ),
                    None,
                )
                if restored_id is not None:
                    for old_token, participant_id in tuple(room.resume_tokens.items()):
                        if participant_id == restored_id:
                            room.resume_tokens.pop(old_token, None)
                token = secrets.token_urlsafe(32)
                participant = Participant(
                    restored_id or uuid.uuid4().hex,
                    name,
                    not room.participants,
                    self._next_cursor_color(room),
                    token,
                )
                room.resume_tokens[token] = participant.participant_id
            room.participants[client_id] = participant
            room.participant_names[participant.participant_id] = participant.username
            room.disconnected_at = None
            self._client_rooms[client_id] = room.code
            return self._change(room, participant, replaced_client_id)

    def save_board(self, client_id: str) -> dict:
        """Persist the current room board after confirming the caller is its host."""
        with self._lock:
            room = self._require_host_room(client_id)
            try:
                saved = self.persistence.save_board(
                    room.code,
                    room.drawing_operations,
                    room.undo_stacks,
                    room.redo_stacks,
                    room.participant_names,
                )
            except PersistenceError as exc:
                raise SessionError(exc.code, str(exc)) from exc
            return {
                "room_code": room.code,
                "saved_at": saved.saved_at,
                "operation_count": len(saved.operations),
            }

    def load_board(self, client_id: str) -> BoardLoadChange:
        """Replace room history from disk and return all room recipients."""
        with self._lock:
            room = self._require_host_room(client_id)
            try:
                saved = self.persistence.load_board(room.code)
            except PersistenceError as exc:
                raise SessionError(exc.code, str(exc)) from exc
            state_size = self._board_state_size(room.code, saved.operations)
            room.drawing_operations = saved.operations
            room.drawing_state_bytes = state_size
            room.undo_stacks = saved.undo_stacks
            room.redo_stacks = saved.redo_stacks
            room.participant_names = saved.participant_names
            operations = [
                {**operation, "points": [point[:] for point in operation["points"]]}
                for operation in room.drawing_operations
            ]
            return BoardLoadChange(room.code, saved.saved_at, operations, tuple(room.participants))

    def _require_host_room(self, client_id: str) -> Room:
        room_code = self._client_rooms.get(client_id)
        if room_code is None:
            raise SessionError("not_in_room", "client must join a room first")
        room = self._rooms[room_code]
        if not room.participants[client_id].is_host:
            raise SessionError("host_only", "only the room host may save or load the board")
        return room

    @staticmethod
    def _board_state_size(room_code: str, operations: list[dict]) -> int:
        try:
            return len(
                encode_message(
                    "board_state", None,
                    {"room_code": room_code, "operations": operations},
                )
            ) - 1
        except ProtocolError as exc:
            raise SessionError("board_too_large", str(exc)) from exc

    def leave_room(self, client_id: str) -> RoomChange | None:
        with self._lock:
            return self._remove_participant(client_id)

    def disconnect(self, client_id: str) -> RoomChange | None:
        """Remove a disconnected client and return an update for survivors."""
        with self._lock:
            return self._remove_participant(client_id, keep_empty=True)

    def add_drawing_operation(self, client_id: str, payload: object) -> DrawingChange:
        """Validate, sequence, store, and route a room member's stroke."""
        with self._lock:
            room_code = self._client_rooms.get(client_id)
            if room_code is None:
                raise SessionError("not_in_room", "client must join a room before drawing")
            room = self._rooms[room_code]
            if len(room.drawing_operations) >= MAX_ROOM_OPERATIONS:
                raise SessionError(
                    "operation_limit", f"rooms may contain at most {MAX_ROOM_OPERATIONS} operations"
                )
            try:
                operation = validate_drawing_operation(payload)
            except DrawingDataError as exc:
                raise SessionError("invalid_drawing_operation", str(exc)) from exc

            operation["sequence_number"] = len(room.drawing_operations) + 1
            operation["participant_id"] = room.participants[client_id].participant_id
            operation["room_code"] = room.code
            operation["active"] = True
            operation_size = len(
                json.dumps(
                    operation, separators=(",", ":"), ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
            )
            candidate_size = room.drawing_state_bytes + operation_size
            if room.drawing_operations:
                candidate_size += 1  # comma between JSON array entries
            if candidate_size > MAX_MESSAGE_SIZE:
                raise SessionError(
                    "operation_limit", "room board has reached the protocol snapshot size limit"
                )
            room.drawing_state_bytes = candidate_size
            room.drawing_operations.append(operation)
            participant_id = room.participants[client_id].participant_id
            room.undo_stacks.setdefault(participant_id, []).append(operation["sequence_number"])
            room.redo_stacks[participant_id] = []
            recipients = tuple(
                other_client_id
                for other_client_id in room.participants
                if other_client_id != client_id
            )
            return DrawingChange(dict(operation), recipients)

    def change_drawing_status(self, client_id: str, action: str) -> DrawingStatusChange:
        """Undo or redo the caller's latest eligible stroke and route its status."""
        with self._lock:
            room_code = self._client_rooms.get(client_id)
            if room_code is None:
                raise SessionError("not_in_room", "client must join a room before undo or redo")
            if action not in {"undo", "redo"}:
                raise SessionError("invalid_drawing_action", "drawing action must be undo or redo")
            room = self._rooms[room_code]
            participant_id = room.participants[client_id].participant_id
            stacks = room.undo_stacks if action == "undo" else room.redo_stacks
            stack = stacks.setdefault(participant_id, [])
            if not stack:
                code = "nothing_to_undo" if action == "undo" else "nothing_to_redo"
                raise SessionError(code, f"there is no drawing operation to {action}")
            sequence = stack.pop()
            operation = next(
                (item for item in room.drawing_operations if item["sequence_number"] == sequence),
                None,
            )
            if operation is None or operation["participant_id"] != participant_id:
                raise SessionError("invalid_drawing_history", "drawing history is inconsistent")
            active = action == "redo"
            if operation["active"] is active:
                raise SessionError("invalid_drawing_history", "drawing operation status is inconsistent")
            operation["active"] = active
            next_stacks = room.redo_stacks if action == "undo" else room.undo_stacks
            next_stacks.setdefault(participant_id, []).append(sequence)
            status = {
                "room_code": room.code,
                "participant_id": participant_id,
                "sequence_number": sequence,
                "active": active,
                "action": action,
            }
            return DrawingStatusChange(status, tuple(room.participants))

    def get_drawing_operations(self, room_code: str) -> list[dict]:
        """Return a copy of the in-memory room canvas history."""
        with self._lock:
            room = self._rooms.get(room_code.upper())
            if room is None:
                raise SessionError("room_not_found", "no room exists with that code")
            return [
                {**operation, "points": [point[:] for point in operation["points"]]}
                for operation in room.drawing_operations
            ]

    def update_cursor(self, client_id: str, payload: object) -> CursorChange:
        """Validate a room member's cursor and route legacy-normalized or world coordinates."""
        with self._lock:
            room_code = self._client_rooms.get(client_id)
            if room_code is None:
                raise SessionError("not_in_room", "client must join a room before moving its cursor")
            if not isinstance(payload, dict):
                raise SessionError("invalid_cursor", "cursor payload must be an object")
            coordinate_space = payload.get("coordinate_space", "normalized")
            allowed = {"x", "y"} if coordinate_space == "normalized" else {
                "x", "y", "coordinate_space"
            }
            if set(payload) != allowed or coordinate_space not in {"normalized", "world"}:
                raise SessionError("invalid_cursor", "cursor payload fields are invalid")

            coordinates = (payload["x"], payload["y"])
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (coordinate_space == "normalized" and not 0 <= value <= 1)
                or (coordinate_space == "world" and abs(value) > MAX_COORDINATE)
                or not math.isfinite(value)
                for value in coordinates
            ):
                raise SessionError("invalid_cursor", "cursor coordinates are outside the allowed range")

            room = self._rooms[room_code]
            participant = room.participants[client_id]
            cursor = {
                "room_code": room.code,
                "participant_id": participant.participant_id,
                "username": participant.username,
                "color": participant.cursor_color,
                "x": float(payload["x"]),
                "y": float(payload["y"]),
            }
            if coordinate_space == "world":
                cursor["coordinate_space"] = "world"
            recipients = tuple(
                other_client_id
                for other_client_id in room.participants
                if other_client_id != client_id
            )
            return CursorChange(cursor, recipients)

    def create_chat_message(self, client_id: str, payload: object) -> ChatChange:
        """Validate room membership and add authoritative sender/time metadata."""
        with self._lock:
            room_code = self._client_rooms.get(client_id)
            if room_code is None:
                raise SessionError("not_in_room", "client must join a room before chatting")
            if not isinstance(payload, dict) or set(payload) != {"text"}:
                raise SessionError("invalid_message", "chat payload must contain only text")
            text = payload["text"]
            if not isinstance(text, str):
                raise SessionError("invalid_message", "chat message text must be a string")
            if not text.strip():
                raise SessionError("empty_message", "chat message must not be empty")
            if len(text) > MAX_CHAT_MESSAGE_LENGTH:
                raise SessionError(
                    "message_too_long",
                    f"chat messages may contain at most {MAX_CHAT_MESSAGE_LENGTH} characters",
                )

            room = self._rooms[room_code]
            participant = room.participants[client_id]
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            message = {
                "room_code": room.code,
                "sender_id": participant.participant_id,
                "username": participant.username,
                "timestamp": timestamp.replace("+00:00", "Z"),
                "text": text.strip(),
            }
            return ChatChange(message, tuple(room.participants))

    def _remove_participant(
        self, client_id: str, *, keep_empty: bool = False
    ) -> RoomChange | None:
        code = self._client_rooms.pop(client_id, None)
        if code is None:
            return None
        room = self._rooms[code]
        departing = room.participants.pop(client_id)
        if not keep_empty:
            room.resume_tokens.pop(departing.resume_token, None)
        if room.participants:
            # Keep the original host while present; otherwise promote the first remaining member.
            if departing.is_host:
                next(iter(room.participants.values())).is_host = True
            room.disconnected_at = None
            return self._change(room, None)
        if keep_empty and self.reconnect_grace_period > 0:
            room.disconnected_at = time.monotonic()
            return RoomChange(code, departing.as_dict(), [], ())
        del self._rooms[code]
        return RoomChange(code, departing.as_dict(), [], ())

    def _change(
        self,
        room: Room,
        participant: Participant | None,
        replaced_client_id: str | None = None,
    ) -> RoomChange:
        return RoomChange(
            room_code=room.code,
            participant=participant.as_dict() if participant else None,
            participants=room.participant_list(),
            recipients=tuple(room.participants),
            resume_token=participant.resume_token if participant else None,
            replaced_client_id=replaced_client_id,
        )

    def _ensure_unassigned(self, client_id: str) -> None:
        if client_id in self._client_rooms:
            raise SessionError("already_in_room", "client is already in a room")

    @staticmethod
    def _validate_username(username: str) -> str:
        if not isinstance(username, str) or not username.strip():
            raise SessionError("invalid_username", "username must not be empty")
        name = username.strip()
        if len(name) > 32:
            raise SessionError("invalid_username", "username must be 32 characters or fewer")
        return name

    @staticmethod
    def _unique_username(room: Room, requested: str) -> str:
        used_names = {participant.username.casefold() for participant in room.participants.values()}
        if requested.casefold() not in used_names:
            return requested
        suffix = 2
        while True:
            ending = f" ({suffix})"
            candidate = f"{requested[:32 - len(ending)]}{ending}"
            if candidate.casefold() not in used_names:
                return candidate
            suffix += 1

    def _new_room_code(self) -> str:
        while True:
            code = "".join(
                secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH)
            )
            if code not in self._rooms:
                return code

    @staticmethod
    def _next_cursor_color(room: Room) -> str:
        used = {participant.cursor_color for participant in room.participants.values()}
        for color in CURSOR_COLORS:
            if color not in used:
                return color
        hue = (len(room.participants) * 0.61803398875) % 1.0
        while True:
            red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.9)
            color = f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"
            if color not in used:
                return color
            hue = (hue + 0.61803398875) % 1.0
