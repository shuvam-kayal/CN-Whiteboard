"""Launch the LAN Collaborative Whiteboard desktop client."""

import sys
import logging

from shared.app_logging import configure_application_logging
from shared.app_paths import is_frozen


logger = logging.getLogger(__name__)


def main() -> int:
    try:
        log_path = configure_application_logging("client", console=not is_frozen())
    except OSError as exc:
        print(f"Could not create the client log file: {exc}", file=sys.stderr)
        return 1

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from client.main_window import MainWindow
    except Exception:
        logger.exception("Whiteboard Client could not load its GUI components")
        raise

    app = QApplication(sys.argv)

    def show_unhandled_error(exc_type, exc_value, traceback) -> None:
        logger.critical(
            "Unhandled client exception",
            exc_info=(exc_type, exc_value, traceback),
        )
        if is_frozen():
            QMessageBox.critical(
                None,
                "Whiteboard Client error",
                f"The client encountered an unexpected error.\n\nDetails were saved to:\n{log_path}",
            )
        else:
            sys.__excepthook__(exc_type, exc_value, traceback)

    sys.excepthook = show_unhandled_error
    try:
        app.setApplicationName("LAN Collaborative Whiteboard")
        window = MainWindow()
        window.show()
        logger.info("Whiteboard Client started")
        result = app.exec()
        logger.info("Whiteboard Client stopped")
        return result
    except Exception as exc:
        logger.exception("Whiteboard Client failed to start or run")
        QMessageBox.critical(
            None,
            "Whiteboard Client error",
            f"The client could not start.\n\n{exc}\n\nDetails were saved to:\n{log_path}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
