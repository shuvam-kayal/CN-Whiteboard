"""Whiteboard layout with mock participants and non-functional placeholders."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class WhiteboardPage(QWidget):
    leave_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 24)
        root.setSpacing(16)
        root.addLayout(self._header())
        root.addWidget(self._toolbar())

        workspace = QHBoxLayout()
        workspace.setSpacing(16)
        workspace.addWidget(self._canvas(), 1)
        workspace.addLayout(self._sidebar(), 0)
        root.addLayout(workspace, 1)

    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        mark = QLabel("W")
        mark.setObjectName("brandMark")
        row.addWidget(mark)
        row.addSpacing(10)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(1)
        title = QLabel("Team whiteboard")
        title.setStyleSheet("font-size: 18px; font-weight: 750;")
        self.room_label = QLabel("Room · —")
        self.room_label.setObjectName("muted")
        self.room_label.setStyleSheet("font-size: 12px;")
        title_stack.addWidget(title)
        title_stack.addWidget(self.room_label)
        row.addLayout(title_stack)
        row.addStretch()

        self.connection_label = QLabel("Preview · no server connection")
        self.connection_label.setObjectName("muted")
        row.addWidget(self.connection_label)
        self.leave_button = QPushButton("Leave preview")
        self.leave_button.setObjectName("quietButton")
        self.leave_button.clicked.connect(self.leave_requested.emit)
        row.addWidget(self.leave_button)
        return row

    @staticmethod
    def _toolbar() -> QFrame:
        frame = QFrame()
        frame.setObjectName("toolbar")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(8)
        label = QLabel("TOOLS")
        label.setObjectName("eyebrow")
        row.addWidget(label)
        row.addSpacing(8)
        for text in ("Select", "Pen", "Eraser", "Text", "Shapes"):
            button = QPushButton(text)
            button.setMinimumWidth(74)
            button.setToolTip("Drawing tools are placeholders in this preview.")
            row.addWidget(button)
        row.addStretch()
        note = QLabel("Toolbar preview")
        note.setObjectName("muted")
        row.addWidget(note)
        return frame

    @staticmethod
    def _canvas() -> QFrame:
        frame = QFrame()
        frame.setObjectName("canvas")
        frame.setMinimumWidth(450)
        frame.setStyleSheet(
            "QFrame#canvas { background-color: #ffffff; border: 1px solid #e2e7f0;"
            " border-radius: 16px; }"
        )
        layout = QVBoxLayout(frame)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark = QLabel("✦")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(
            "background: #eef0ff; color: #5b63e8; border-radius: 28px;"
            " font-size: 24px; min-width: 56px; max-width: 56px;"
            " min-height: 56px; max-height: 56px;"
        )
        title = QLabel("Your canvas starts here")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #34405b;")
        description = QLabel("The drawing surface will be available in a future slice.")
        description.setObjectName("muted")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        layout.addWidget(mark, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(12)
        layout.addWidget(title)
        layout.addWidget(description)
        return frame

    def _sidebar(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(14)
        layout.addWidget(self._participants_card(), 1)
        layout.addWidget(self._chat_card(), 1)
        return layout

    def _participants_card(self) -> QFrame:
        card = self._sidebar_card("Participants", "3 in this preview")
        self.participant_list = QListWidget()
        self.participant_list.setObjectName("participantList")
        self.participant_list.setMinimumWidth(250)
        card.layout().addWidget(self.participant_list, 1)
        return card

    def _chat_card(self) -> QFrame:
        card = self._sidebar_card("Room chat", "Preview only")
        chat_placeholder = QLabel(
            "Chat will appear here\n\nMessaging is not connected in this preview."
        )
        chat_placeholder.setObjectName("muted")
        chat_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chat_placeholder.setWordWrap(True)
        card.layout().addWidget(chat_placeholder, 1)
        return card

    @staticmethod
    def _sidebar_card(title: str, subtitle: str) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumHeight(190)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(5)
        heading = QHBoxLayout()
        heading_title = QLabel(title)
        heading_title.setStyleSheet("font-weight: 700; font-size: 15px;")
        heading.addWidget(heading_title)
        heading.addStretch()
        small = QLabel(subtitle)
        small.setObjectName("muted")
        small.setStyleSheet("font-size: 11px;")
        layout.addLayout(heading)
        layout.addWidget(small)
        return card

    def set_room(
        self,
        username: str,
        room_code: str,
        is_host: bool,
        server_address: str,
        server_port: int,
    ) -> None:
        """Populate this screen with temporary local preview data."""
        role = "Host" if is_host else "Participant"
        self.room_label.setText(f"Room · {room_code}  /  {role}")
        self.connection_label.setText(
            f"Preview settings · {server_address}:{server_port} (not connected)"
        )
        self.participant_list.clear()
        self.participant_list.addItem(f"●  {username}  ·  You")
        self.participant_list.addItem("●  Morgan Lee")
        self.participant_list.addItem("●  Avery Chen")
