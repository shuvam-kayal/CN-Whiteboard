import json

import pytest

from shared.protocol import (
    MAX_MESSAGE_SIZE,
    MessageBuffer,
    ProtocolError,
    decode_message,
    encode_message,
)


def test_encode_and_decode_message() -> None:
    encoded = encode_message("echo", "request-1", {"text": "hello"})
    assert encoded.endswith(b"\n")
    assert decode_message(encoded) == {
        "type": "echo",
        "request_id": "request-1",
        "payload": {"text": "hello"},
    }


def test_buffer_reads_message_split_across_chunks() -> None:
    buffer = MessageBuffer()
    encoded = encode_message("echo", "split", "payload")
    split = len(encoded) // 2
    assert buffer.feed(encoded[:split]) == []
    assert buffer.feed(encoded[split:]) == [
        {"type": "echo", "request_id": "split", "payload": "payload"}
    ]


def test_buffer_reads_multiple_messages_from_one_chunk() -> None:
    buffer = MessageBuffer()
    combined = encode_message("echo", "one", 1) + encode_message("echo", "two", 2)
    assert buffer.feed(combined) == [
        {"type": "echo", "request_id": "one", "payload": 1},
        {"type": "echo", "request_id": "two", "payload": 2},
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b"{not-json}\n",
        b"[]\n",
        b'{"type":"echo","request_id":"id"}\n',
        b'{"type":"","request_id":"id","payload":null}\n',
        b'{"type":"echo","request_id":3,"payload":null}\n',
        b'{"type":"echo","request_id":"id","payload":NaN}\n',
    ],
)
def test_buffer_rejects_invalid_messages(raw: bytes) -> None:
    with pytest.raises(ProtocolError):
        MessageBuffer().feed(raw)


def test_buffer_rejects_oversized_complete_message() -> None:
    oversized = b" " * (MAX_MESSAGE_SIZE + 1) + b"\n"
    with pytest.raises(ProtocolError, match="exceeds"):
        MessageBuffer().feed(oversized)


def test_buffer_rejects_oversized_partial_message() -> None:
    with pytest.raises(ProtocolError, match="exceeds"):
        MessageBuffer().feed(b" " * (MAX_MESSAGE_SIZE + 1))


def test_encoder_rejects_non_json_payload() -> None:
    with pytest.raises(ProtocolError, match="JSON-serializable"):
        encode_message("echo", "id", {"invalid": object()})


def test_decoder_rejects_extra_lines() -> None:
    raw = json.dumps({"type": "echo", "request_id": "id", "payload": None})
    with pytest.raises(ProtocolError, match="exactly one"):
        decode_message(raw + "\n\n")
