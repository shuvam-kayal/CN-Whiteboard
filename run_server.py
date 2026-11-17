"""Command-line entry point for the TCP whiteboard server."""

import argparse
import logging

from shared.app_logging import configure_application_logging
from shared.app_paths import boards_directory, logs_directory
from server.tcp_server import TCPServer


logger = logging.getLogger(__name__)


def main() -> None:
    configure_application_logging("server", console=True)
    parser = argparse.ArgumentParser(description="Run the whiteboard TCP server.")
    parser.add_argument("--host", default="0.0.0.0", help="IPv4 address to bind")
    parser.add_argument("--port", type=int, default=5000, help="TCP port to bind")
    args = parser.parse_args()

    boards_directory().mkdir(parents=True, exist_ok=True)
    logs_directory().mkdir(parents=True, exist_ok=True)
    server = TCPServer(host=args.host, port=args.port)
    logger.info("Starting Whiteboard Server on %s:%s", args.host, args.port)
    logger.info("Board files: %s", server.persistence.boards_directory)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutdown requested from console")
    except Exception:
        logger.exception("Whiteboard Server stopped because of an unexpected error")
        raise
    finally:
        server.shutdown()
        logger.info("Whiteboard Server stopped")


if __name__ == "__main__":
    main()
