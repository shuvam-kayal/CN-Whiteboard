"""Interactive terminal client for testing the raw TCP echo server."""

import argparse
import itertools
import socket

from shared.protocol import MessageBuffer, ProtocolError, encode_message


def main() -> None:
    parser = argparse.ArgumentParser(description="Send raw text to the TCP echo server.")
    parser.add_argument("--host", default="127.0.0.1", help="server IPv4 address")
    parser.add_argument("--port", type=int, default=5000, help="server TCP port")
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port)) as connection:
        print(f"Connected to {args.host}:{args.port}. Enter text; type /quit to exit.")
        message_buffer = MessageBuffer()
        request_ids = itertools.count(1)
        pending_responses = []
        while True:
            try:
                message = input("> ")
            except EOFError:
                break
            if message == "/quit":
                break
            if not message:
                continue
            request_id = str(next(request_ids))
            connection.sendall(encode_message("echo", request_id, {"text": message}))
            try:
                while not pending_responses:
                    chunk = connection.recv(4096)
                    if not chunk:
                        print("Server disconnected.")
                        return
                    pending_responses.extend(message_buffer.feed(chunk))
            except ProtocolError as exc:
                print(f"Invalid server response: {exc}")
                return
            response = pending_responses.pop(0)
            if response["type"] == "protocol_error":
                print(f"Server rejected the message: {response['payload']}")
                return
            print(f"Echo: {response['payload']}")


if __name__ == "__main__":
    main()
