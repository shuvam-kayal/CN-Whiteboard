"""Welcome and room entry form for the desktop client."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class WelcomePage(QWidget):
    create_requested = Signal(str, str, int)
    join_requested = Signal(str, str, str, int)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(48, 30, 48, 32)
        root.setSpacing(28)
        root.addLayout(self._brand_header())

        body = QHBoxLayout()
        body.setSpacing(54)
        body.addLayout(self._hero(), 1)
        body.addWidget(self._form_card(), 1)
        root.addLayout(body, 1)

        footer = QLabel("A local preview · Room and server details are mock data in this slice")
        footer.setObjectName("muted")
        root.addWidget(footer, alignment=Qt.AlignmentFlag.AlignLeft)

    @staticmethod
    def _brand_header() -> QHBoxLayout:
        row = QHBoxLayout()
        mark = QLabel("W")
        mark.setObjectName("brandMark")
        name = QLabel("Whiteboard")
        name.setStyleSheet("font-size: 16px; font-weight: 750; color: #252f49;")
        row.addWidget(mark)
        row.addSpacing(9)
        row.addWidget(name)
        row.addStretch()
        badge = QLabel("DESKTOP PREVIEW")
        badge.setObjectName("eyebrow")
        row.addWidget(badge)
        return row

    @staticmethod
    def _hero() -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.addStretch(1)
        eyebrow = QLabel("LAN COLLABORATIVE WHITEBOARD")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("Make space\nfor good ideas.")
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 43px; font-weight: 750; color: #202943;")
        description = QLabel(
            "Bring your team into one shared room. Create a new space or join with a room code."
        )
        description.setWordWrap(True)
        description.setObjectName("muted")
        description.setStyleSheet("font-size: 16px;")

        feature = QFrame()
        feature.setObjectName("card")
        feature_layout = QVBoxLayout(feature)
        feature_layout.setContentsMargins(18, 16, 18, 16)
        feature_layout.setSpacing(6)
        feature_title = QLabel("A calm space to think together")
        feature_title.setStyleSheet("font-weight: 700;")
        feature_text = QLabel("Rooms, participants, and a canvas preview are ready to explore.")
        feature_text.setObjectName("muted")
        feature_text.setWordWrap(True)
        feature_layout.addWidget(feature_title)
        feature_layout.addWidget(feature_text)

        layout.addWidget(eyebrow)
        layout.addSpacing(10)
        layout.addWidget(title)
        layout.addSpacing(12)
        layout.addWidget(description)
        layout.addSpacing(22)
        layout.addWidget(feature)
        layout.addStretch(2)
        return layout

    def _form_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 25, 26, 25)
        layout.setSpacing(13)

        title = QLabel("Get started")
        title.setStyleSheet("font-size: 23px; font-weight: 750;")
        subtitle = QLabel("Set your name and choose how to enter.")
        subtitle.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(4)

        layout.addWidget(self._field_label("Your name"))
        self.username_input = QLineEdit()
        self.username_input.setObjectName("usernameInput")
        self.username_input.setPlaceholderText("e.g. Jordan")
        layout.addWidget(self.username_input)

        layout.addWidget(self._field_label("Server connection"))
        connection_row = QHBoxLayout()
        connection_row.setSpacing(10)
        self.server_address_input = QLineEdit("127.0.0.1")
        self.server_address_input.setObjectName("serverAddressInput")
        self.server_address_input.setPlaceholderText("IPv4 address")
        self.port_input = QSpinBox()
        self.port_input.setObjectName("serverPortInput")
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(5000)
        self.port_input.setFixedWidth(112)
        connection_row.addWidget(self.server_address_input, 1)
        connection_row.addWidget(self.port_input)
        layout.addLayout(connection_row)

        self.create_button = QPushButton("Create a new room")
        self.create_button.setObjectName("primaryButton")
        self.create_button.setMinimumHeight(44)
        self.create_button.clicked.connect(self._create_room)
        layout.addWidget(self.create_button)

        separator = QHBoxLayout()
        separator.addWidget(self._line())
        or_label = QLabel("OR JOIN A ROOM")
        or_label.setObjectName("eyebrow")
        separator.addWidget(or_label)
        separator.addWidget(self._line())
        layout.addSpacing(2)
        layout.addLayout(separator)

        join_row = QHBoxLayout()
        self.room_code_input = QLineEdit()
        self.room_code_input.setObjectName("roomCodeInput")
        self.room_code_input.setPlaceholderText("Room code")
        self.room_code_input.setMaxLength(12)
        self.join_button = QPushButton("Join room")
        self.join_button.setObjectName("joinButton")
        self.join_button.setMinimumHeight(42)
        self.join_button.clicked.connect(self._join_room)
        join_row.addWidget(self.room_code_input, 1)
        join_row.addWidget(self.join_button)
        layout.addLayout(join_row)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b53c4b; font-size: 12px;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        layout.addStretch()
        return card

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("eyebrow")
        label.setStyleSheet("font-size: 12px; color: #515e77;")
        return label

    @staticmethod
    def _line() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #e4e8f0;")
        return line

    def _create_room(self) -> None:
        username = self._username()
        if username is None:
            return
        address = self.server_address_input.text().strip()
        if not address:
            self.error_label.setText("Enter the server IPv4 address.")
            return
        self.error_label.clear()
        self.create_requested.emit(username, address, self.port_input.value())

    def _join_room(self) -> None:
        username = self._username()
        if username is None:
            return
        room_code = self.room_code_input.text().strip().upper()
        if not room_code:
            self.error_label.setText("Enter a room code to join.")
            return
        address = self.server_address_input.text().strip()
        if not address:
            self.error_label.setText("Enter the server IPv4 address.")
            return
        self.error_label.clear()
        self.join_requested.emit(
            username, room_code, address, self.port_input.value()
        )

    def _username(self) -> str | None:
        username = self.username_input.text().strip()
        if not username:
            self.error_label.setText("Enter your name to continue.")
            self.username_input.setFocus()
            return None
        self.error_label.clear()
        return username
