"""Interactive terminal client for testing the raw TCP echo server."""

import argparse
import socket


def main() -> None:
    parser = argparse.ArgumentParser(description="Send raw text to the TCP echo server.")
    parser.add_argument("--host", default="127.0.0.1", help="server IPv4 address")
    parser.add_argument("--port", type=int, default=5000, help="server TCP port")
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port)) as connection:
        print(f"Connected to {args.host}:{args.port}. Enter text; type /quit to exit.")
        while True:
            try:
                message = input("> ")
            except EOFError:
                break
            if message == "/quit":
                break
            payload = message.encode("utf-8")
            if not payload:
                continue
            connection.sendall(payload)
            echoed = bytearray()
            while len(echoed) < len(payload):
                chunk = connection.recv(len(payload) - len(echoed))
                if not chunk:
                    print("Server disconnected.")
                    return
                echoed.extend(chunk)
            print(f"Echo: {echoed.decode('utf-8', errors='replace')}")


if __name__ == "__main__":
    main()
