"""Command-line entry point for the TCP echo server."""

import argparse

from server.tcp_server import TCPServer


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the whiteboard TCP server.")
    parser.add_argument("--host", default="0.0.0.0", help="IPv4 address to bind")
    parser.add_argument("--port", type=int, default=5000, help="TCP port to bind")
    args = parser.parse_args()

    server = TCPServer(host=args.host, port=args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
