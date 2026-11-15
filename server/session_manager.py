"""Thread-safe room and participant state, independent of socket handling."""

from dataclasses import dataclass
import secrets
import threading
import uuid


ROOM_CODE_LENGTH = 6
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


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

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "participant_id": self.participant_id,
            "username": self.username,
            "is_host": self.is_host,
        }


@dataclass
class Room:
    code: str
    participants: dict[str, Participant]

    def participant_list(self) -> list[dict[str, str | bool]]:
        return [participant.as_dict() for participant in self.participants.values()]


@dataclass
class RoomChange:
    room_code: str
    participant: dict[str, str | bool] | None
    participants: list[dict[str, str | bool]]
    recipients: tuple[str, ...]


class SessionManager:
    """Manage independent rooms and map connected clients to memberships."""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._client_rooms: dict[str, str] = {}
        self._lock = threading.RLock()

    @property
    def room_count(self) -> int:
        with self._lock:
            return len(self._rooms)

    def create_room(self, client_id: str, username: str) -> RoomChange:
        with self._lock:
            self._ensure_unassigned(client_id)
            name = self._validate_username(username)
            code = self._new_room_code()
            participant = Participant(uuid.uuid4().hex, name, True)
            room = Room(code, {client_id: participant})
            self._rooms[code] = room
            self._client_rooms[client_id] = code
            return self._change(room, participant)

    def join_room(self, client_id: str, room_code: str, username: str) -> RoomChange:
        with self._lock:
            self._ensure_unassigned(client_id)
            if not isinstance(room_code, str) or len(room_code) != ROOM_CODE_LENGTH or any(
                character not in ROOM_CODE_ALPHABET for character in room_code.upper()
            ):
                raise SessionError("invalid_room_code", "room code has an invalid format")
            normalized_code = room_code.upper()
            room = self._rooms.get(normalized_code)
            if room is None:
                raise SessionError("room_not_found", "no room exists with that code")
            requested_name = self._validate_username(username)
            name = self._unique_username(room, requested_name)
            participant = Participant(uuid.uuid4().hex, name, False)
            room.participants[client_id] = participant
            self._client_rooms[client_id] = room.code
            return self._change(room, participant)

    def leave_room(self, client_id: str) -> RoomChange | None:
        with self._lock:
            return self._remove_participant(client_id)

    def disconnect(self, client_id: str) -> RoomChange | None:
        """Remove a disconnected client and return an update for survivors."""
        with self._lock:
            return self._remove_participant(client_id)

    def _remove_participant(self, client_id: str) -> RoomChange | None:
        code = self._client_rooms.pop(client_id, None)
        if code is None:
            return None
        room = self._rooms[code]
        departing = room.participants.pop(client_id)
        if room.participants:
            # Keep the original host while present; otherwise promote the first remaining member.
            if departing.is_host:
                next(iter(room.participants.values())).is_host = True
            return self._change(room, None)
        del self._rooms[code]
        return RoomChange(code, departing.as_dict(), [], ())

    def _change(
        self,
        room: Room,
        participant: Participant | None,
    ) -> RoomChange:
        return RoomChange(
            room_code=room.code,
            participant=participant.as_dict() if participant else None,
            participants=room.participant_list(),
            recipients=tuple(room.participants),
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
