"""Top-level navigation between the welcome and whiteboard preview screens."""

from PySide6.QtWidgets import QMainWindow, QStackedWidget

from client.welcome_page import WelcomePage
from client.whiteboard_page import WhiteboardPage


class MainWindow(QMainWindow):
    """Own the desktop pages without coupling the UI to networking code."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LAN Collaborative Whiteboard")
        self.resize(1180, 780)
        self.setMinimumSize(900, 640)

        self.pages = QStackedWidget()
        self.welcome_page = WelcomePage()
        self.whiteboard_page = WhiteboardPage()
        self.pages.addWidget(self.welcome_page)
        self.pages.addWidget(self.whiteboard_page)
        self.setCentralWidget(self.pages)
        self.setStyleSheet(APP_STYLE)

        self.welcome_page.create_requested.connect(self._create_preview_room)
        self.welcome_page.join_requested.connect(self._join_preview_room)
        self.whiteboard_page.leave_requested.connect(self._show_welcome)

    def _create_preview_room(self, username: str, address: str, port: int) -> None:
        # This slice uses local mock state; these settings are not connected to a server.
        self.whiteboard_page.set_room(
            username=username,
            room_code="WAVE24",
            is_host=True,
            server_address=address,
            server_port=port,
        )
        self.pages.setCurrentWidget(self.whiteboard_page)

    def _join_preview_room(
        self, username: str, room_code: str, address: str, port: int
    ) -> None:
        self.whiteboard_page.set_room(
            username=username,
            room_code=room_code,
            is_host=False,
            server_address=address,
            server_port=port,
        )
        self.pages.setCurrentWidget(self.whiteboard_page)

    def _show_welcome(self) -> None:
        self.pages.setCurrentWidget(self.welcome_page)


APP_STYLE = """
QMainWindow {
    background: #f3f5fa;
}
QWidget {
    color: #202943;
    font-family: "Segoe UI";
    font-size: 14px;
}
QLabel#eyebrow { color: #66728b; font-size: 11px; font-weight: 700; }
QLabel#muted { color: #7b879d; }
QLabel#brandMark {
    background: #5b63e8; color: white; border-radius: 13px;
    font-size: 16px; font-weight: 800; min-width: 34px; max-width: 34px;
    min-height: 34px; max-height: 34px; qproperty-alignment: AlignCenter;
}
QFrame#card, QFrame#toolbar {
    background: white; border: 1px solid #e2e7f0; border-radius: 16px;
}
QLineEdit, QSpinBox {
    background: white; border: 1px solid #dce2ed; border-radius: 9px;
    padding: 10px 12px; color: #202943; min-height: 20px;
}
QLineEdit:focus, QSpinBox:focus { border: 1px solid #7078ee; }
QComboBox {
    background: white; border: 1px solid #dce2ed; border-radius: 9px;
    padding: 8px 10px; color: #202943; min-height: 20px;
}
QComboBox:focus { border: 1px solid #7078ee; }
QPushButton {
    background: white; border: 1px solid #dce2ed; border-radius: 9px;
    color: #35405a; padding: 10px 14px; font-weight: 600;
}
QPushButton:hover { background: #f7f8ff; border-color: #aeb4f5; }
QPushButton:checked {
    background: #eef0ff; border-color: #9ca3f2; color: #3f47c2;
}
QPushButton#primaryButton {
    background: #5b63e8; border: 1px solid #5b63e8; color: white;
}
QPushButton#primaryButton:hover { background: #4e56d5; }
QPushButton#quietButton { background: transparent; border: none; color: #66728b; }
QListWidget {
    background: transparent; border: none; outline: none; padding: 2px;
}
QListWidget::item { padding: 9px 6px; border-radius: 7px; }
QListWidget::item:selected { background: #eef0ff; color: #3039bb; }
"""
