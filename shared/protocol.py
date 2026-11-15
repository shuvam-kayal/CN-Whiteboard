"""Newline-delimited JSON message encoding and buffered decoding."""

import json
from typing import Any


MAX_MESSAGE_SIZE = 1_048_576


class ProtocolError(ValueError):
    """Raised when a message is malformed or violates protocol limits."""


def _validate_message(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    if not isinstance(message.get("type"), str) or not message["type"].strip():
        raise ProtocolError("message 'type' must be a non-empty string")
    request_id = message.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise ProtocolError("message 'request_id' must be a string or null")
    if "payload" not in message:
        raise ProtocolError("message is missing required field 'payload'")
    return message


def encode_message(message_type: str, request_id: str | None, payload: Any) -> bytes:
    """Validate and encode one message, including its terminating newline."""
    message = _validate_message(
        {"type": message_type, "request_id": request_id, "payload": payload}
    )
    try:
        encoded = (
            json.dumps(
                message,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"message payload is not JSON-serializable: {exc}") from exc
    if len(encoded) - 1 > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"message exceeds {MAX_MESSAGE_SIZE} bytes")
    return encoded


def decode_message(data: bytes | str) -> dict[str, Any]:
    """Decode and validate exactly one JSON message, with an optional newline."""
    if isinstance(data, bytes):
        if data.endswith(b"\r\n"):
            content = data[:-2]
        elif data.endswith(b"\n"):
            content = data[:-1]
        else:
            content = data
        if len(content) > MAX_MESSAGE_SIZE:
            raise ProtocolError(f"message exceeds {MAX_MESSAGE_SIZE} bytes")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("message must be valid UTF-8") from exc
    elif isinstance(data, str):
        if data.endswith("\r\n"):
            text = data[:-2]
        elif data.endswith("\n"):
            text = data[:-1]
        else:
            text = data
        if len(text.encode("utf-8")) > MAX_MESSAGE_SIZE:
            raise ProtocolError(f"message exceeds {MAX_MESSAGE_SIZE} bytes")
    else:
        raise ProtocolError("message data must be bytes or str")

    if "\n" in text or "\r" in text:
        raise ProtocolError("expected exactly one message")
    try:
        message = json.loads(
            text,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProtocolError(f"invalid JSON constant: {value}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"malformed JSON: {exc.msg}") from exc
    return _validate_message(message)


class MessageBuffer:
    """Collect arbitrary TCP chunks and return complete decoded messages."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        """Add a TCP chunk and return every complete message it contains."""
        if not isinstance(data, bytes):
            raise ProtocolError("TCP data must be bytes")
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []

        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > MAX_MESSAGE_SIZE:
                    self._buffer.clear()
                    raise ProtocolError(f"message exceeds {MAX_MESSAGE_SIZE} bytes")
                break
            if newline > MAX_MESSAGE_SIZE:
                self._buffer.clear()
                raise ProtocolError(f"message exceeds {MAX_MESSAGE_SIZE} bytes")
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            messages.append(decode_message(line))
        return messages
