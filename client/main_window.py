"""Top-level GUI navigation and queued handling of server events."""

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QMainWindow, QStackedWidget

from client.network_client import NetworkClient
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
        self.network_client: NetworkClient | None = None
        self.room_code: str | None = None
        self.participant_id: str | None = None
        self.pages.addWidget(self.welcome_page)
        self.pages.addWidget(self.whiteboard_page)
        self.setCentralWidget(self.pages)
        self.setStyleSheet(APP_STYLE)

        self.welcome_page.create_requested.connect(self._create_room)
        self.welcome_page.join_requested.connect(self._join_room)
        self.whiteboard_page.leave_requested.connect(self._leave_room)
        self.whiteboard_page.drawing_operation_completed.connect(
            self._send_drawing_operation
        )

    def _create_room(self, username: str, address: str, port: int) -> None:
        self._connect_to_room(
            username, address, port, "create_room", {"username": username}
        )

    def _join_room(
        self, username: str, room_code: str, address: str, port: int
    ) -> None:
        self._connect_to_room(
            username,
            address,
            port,
            "join_room",
            {"username": username, "room_code": room_code},
        )

    def _connect_to_room(
        self,
        username: str,
        address: str,
        port: int,
        message_type: str,
        payload: dict,
    ) -> None:
        self._close_network_client()
        self.room_code = None
        self.participant_id = None
        self.welcome_page.show_status(f"Connecting to {address}:{port}…")
        client = NetworkClient(address, port, message_type, payload, self)
        client.message_received.connect(self._on_server_message)
        client.disconnected.connect(self._on_disconnected)
        self.network_client = client
        client.start()

    def _close_network_client(self) -> None:
        client = self.network_client
        self.network_client = None
        if client is not None:
            client.close()

    def _leave_room(self) -> None:
        self._close_network_client()
        self.room_code = None
        self.participant_id = None
        self.pages.setCurrentWidget(self.welcome_page)
        self.welcome_page.show_status("")

    @Slot(object)
    def _on_server_message(self, message: object) -> None:
        """Apply network events on the GUI thread via Qt's queued signal delivery."""
        if not isinstance(message, dict):
            return
        message_type = message.get("type")
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return

        if message_type in {"room_created", "room_joined"}:
            room_code = payload.get("room_code")
            participant_id = payload.get("participant_id")
            if not isinstance(room_code, str) or not isinstance(participant_id, str):
                self.welcome_page.show_status("Server returned invalid room details.", is_error=True)
                return
            self.room_code = room_code
            self.participant_id = participant_id
            client = self.network_client
            if client is None:
                return
            self.whiteboard_page.set_room(
                username=str(payload.get("username", "Participant")),
                room_code=room_code,
                is_host=bool(payload.get("is_host", False)),
                server_address=client.address,
                server_port=client.port,
                participant_id=participant_id,
            )
            self.pages.setCurrentWidget(self.whiteboard_page)
        elif message_type == "participants_updated":
            if payload.get("room_code") == self.room_code:
                participants = payload.get("participants")
                if isinstance(participants, list):
                    self.whiteboard_page.update_participants(participants)
        elif message_type == "drawing_accepted":
            if payload.get("room_code") == self.room_code:
                request_id = message.get("request_id")
                sequence = payload.get("sequence_number")
                if isinstance(request_id, str) and isinstance(sequence, int):
                    self.whiteboard_page.confirm_local_operation(request_id, sequence)
        elif message_type == "drawing_operation":
            if (
                payload.get("room_code") == self.room_code
                and payload.get("participant_id") != self.participant_id
            ):
                try:
                    self.whiteboard_page.add_remote_operation(payload)
                except ValueError as exc:
                    self.whiteboard_page.set_connection_status(
                        f"Rejected drawing update: {exc}"
                    )
        elif message_type == "error":
            message_text = str(payload.get("message", "Server rejected the request."))
            if self.pages.currentWidget() is self.whiteboard_page:
                self.whiteboard_page.set_connection_status(message_text)
            else:
                self.welcome_page.show_status(message_text, is_error=True)

    @Slot(str)
    def _on_disconnected(self, reason: str) -> None:
        if self.pages.currentWidget() is self.whiteboard_page:
            self.whiteboard_page.set_connection_status(f"Disconnected · {reason}")
        else:
            self.welcome_page.show_status(f"Connection failed · {reason}", is_error=True)

    @Slot(str, object)
    def _send_drawing_operation(self, operation_id: str, payload: object) -> None:
        client = self.network_client
        if client is None or not isinstance(payload, dict):
            return
        if not client.send_drawing_operation(operation_id, payload):
            self.whiteboard_page.set_connection_status("Drawing was not sent · disconnected")

    def closeEvent(self, event) -> None:
        self._close_network_client()
        super().closeEvent(event)


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
