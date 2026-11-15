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
        self.whiteboard_page.cursor_position_changed.connect(
            self._send_cursor_position
        )
        self.whiteboard_page.chat_message_requested.connect(self._send_chat_message)
        self.whiteboard_page.save_board_requested.connect(self._save_board)
        self.whiteboard_page.load_board_requested.connect(self._load_board)
        self.whiteboard_page.undo_requested.connect(self._undo_drawing)
        self.whiteboard_page.redo_requested.connect(self._redo_drawing)

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
        self._close_network_client(leave_room=True)
        self.room_code = None
        self.participant_id = None
        self.welcome_page.show_status(f"Connecting to {address}:{port}…")
        client = NetworkClient(address, port, message_type, payload, self)
        client.message_received.connect(self._on_server_message)
        client.disconnected.connect(self._on_disconnected)
        client.reconnecting.connect(self._on_reconnecting)
        self.network_client = client
        client.start()

    def _close_network_client(self, *, leave_room: bool = False) -> None:
        client = self.network_client
        self.network_client = None
        if client is not None:
            if leave_room:
                client.send_message("leave_room", {})
            client.close()

    def _leave_room(self) -> None:
        self._close_network_client(leave_room=True)
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
            previous_room = self.room_code
            previous_participant = self.participant_id
            same_membership = (
                previous_room == room_code and previous_participant == participant_id
            )
            self.room_code = room_code
            self.participant_id = participant_id
            client = self.network_client
            if client is None:
                return
            if same_membership:
                self.whiteboard_page.set_host_status(bool(payload.get("is_host", False)))
                self.whiteboard_page.set_connection_status(
                    f"Connected · {client.address}:{client.port}"
                )
            else:
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
        elif message_type == "drawing_operation_status":
            if payload.get("room_code") == self.room_code:
                sequence = payload.get("sequence_number")
                active = payload.get("active")
                if isinstance(sequence, int) and not isinstance(sequence, bool) and isinstance(active, bool):
                    self.whiteboard_page.set_operation_active(sequence, active)
        elif message_type == "cursor_update":
            if (
                payload.get("room_code") == self.room_code
                and payload.get("participant_id") != self.participant_id
            ):
                self.whiteboard_page.set_remote_cursor(payload)
        elif message_type == "chat_message":
            if payload.get("room_code") == self.room_code:
                self.whiteboard_page.append_chat_message(payload)
        elif message_type == "board_state":
            if payload.get("room_code") == self.room_code:
                try:
                    self.whiteboard_page.replace_board_state(payload.get("operations"))
                except ValueError as exc:
                    self.whiteboard_page.set_persistence_status(
                        f"Rejected board state: {exc}", is_error=True
                    )
        elif message_type == "board_saved":
            if payload.get("room_code") == self.room_code:
                self.whiteboard_page.set_persistence_status(
                    f"Saved {payload.get('operation_count', 0)} drawing operations."
                )
        elif message_type == "board_loaded":
            if payload.get("room_code") == self.room_code:
                self.whiteboard_page.set_persistence_status("Board loaded and synchronized.")
        elif message_type == "error":
            message_text = str(payload.get("message", "Server rejected the request."))
            if payload.get("code") in {
                "empty_message",
                "message_too_long",
                "invalid_message",
            }:
                self.whiteboard_page.show_chat_status(message_text)
            elif payload.get("code") in {
                "host_only", "board_not_found", "invalid_board", "save_failed",
                "board_too_large", "board_restore_failed", "not_in_room",
                "nothing_to_undo", "nothing_to_redo", "invalid_drawing_history",
            } and self.pages.currentWidget() is self.whiteboard_page:
                self.whiteboard_page.set_persistence_status(message_text, is_error=True)
            elif self.pages.currentWidget() is self.whiteboard_page:
                self.whiteboard_page.set_connection_status(message_text)
            else:
                self.welcome_page.show_status(message_text, is_error=True)

    @Slot(str)
    def _on_disconnected(self, reason: str) -> None:
        if self.pages.currentWidget() is self.whiteboard_page:
            self.whiteboard_page.set_connection_status(f"Disconnected · {reason}")
        else:
            self.welcome_page.show_status(f"Connection failed · {reason}", is_error=True)

    @Slot(str)
    def _on_reconnecting(self, message: str) -> None:
        if self.pages.currentWidget() is self.whiteboard_page:
            self.whiteboard_page.set_connection_status(message)
        else:
            self.welcome_page.show_status(message, is_error=True)

    @Slot(str, object)
    def _send_drawing_operation(self, operation_id: str, payload: object) -> None:
        client = self.network_client
        if client is None or not isinstance(payload, dict):
            return
        if not client.send_drawing_operation(operation_id, payload):
            self.whiteboard_page.set_connection_status("Drawing was not sent · disconnected")

    @Slot(float, float)
    def _send_cursor_position(self, x: float, y: float) -> None:
        client = self.network_client
        if client is not None:
            client.send_cursor_position(x, y)

    @Slot(str)
    def _send_chat_message(self, text: str) -> None:
        client = self.network_client
        if client is None or not client.send_chat_message(text):
            self.whiteboard_page.show_chat_status("Message was not sent · disconnected")

    def _save_board(self) -> None:
        client = self.network_client
        if client is None or not client.send_message("save_board", {}):
            self.whiteboard_page.set_persistence_status("Board save was not sent · disconnected", is_error=True)

    def _load_board(self) -> None:
        client = self.network_client
        if client is None or not client.send_message("load_board", {}):
            self.whiteboard_page.set_persistence_status("Board load was not sent · disconnected", is_error=True)

    def _undo_drawing(self) -> None:
        client = self.network_client
        if client is None or not client.send_message("undo_operation", {}):
            self.whiteboard_page.set_connection_status("Undo was not sent · disconnected")

    def _redo_drawing(self) -> None:
        client = self.network_client
        if client is None or not client.send_message("redo_operation", {}):
            self.whiteboard_page.set_connection_status("Redo was not sent · disconnected")

    def closeEvent(self, event) -> None:
        self._close_network_client(leave_room=True)
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
