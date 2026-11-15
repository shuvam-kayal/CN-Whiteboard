"""Whiteboard layout, local canvas, and room participant display."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from client.drawing_canvas import DrawingCanvas


class WhiteboardPage(QWidget):
    leave_requested = Signal()
    drawing_operation_completed = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 24)
        root.setSpacing(16)
        self.canvas = DrawingCanvas()
        self.canvas.operation_completed.connect(self.drawing_operation_completed.emit)
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

        self.connection_label = QLabel("Not connected")
        self.connection_label.setObjectName("muted")
        row.addWidget(self.connection_label)
        self.leave_button = QPushButton("Leave room")
        self.leave_button.setObjectName("quietButton")
        self.leave_button.clicked.connect(self.leave_requested.emit)
        row.addWidget(self.leave_button)
        return row

    def _toolbar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("toolbar")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(8)
        label = QLabel("TOOLS")
        label.setObjectName("eyebrow")
        row.addWidget(label)
        row.addSpacing(8)
        self.pen_button = QPushButton("Pen")
        self.pen_button.setObjectName("penToolButton")
        self.pen_button.setCheckable(True)
        self.pen_button.setChecked(True)
        self.eraser_button = QPushButton("Eraser")
        self.eraser_button.setObjectName("eraserToolButton")
        self.eraser_button.setCheckable(True)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_group.addButton(self.pen_button)
        self.tool_group.addButton(self.eraser_button)
        self.pen_button.clicked.connect(lambda: self.canvas.set_tool("pen"))
        self.eraser_button.clicked.connect(lambda: self.canvas.set_tool("eraser"))
        row.addWidget(self.pen_button)
        row.addWidget(self.eraser_button)

        color_label = QLabel("Color")
        color_label.setObjectName("muted")
        row.addWidget(color_label)
        self.color_combo = QComboBox()
        self.color_combo.setObjectName("penColorCombo")
        for name, color in (
            ("Midnight", "#202943"),
            ("Blue", "#3867d6"),
            ("Coral", "#e05a5a"),
            ("Green", "#279777"),
            ("Violet", "#8858c8"),
            ("Orange", "#dd812f"),
        ):
            self.color_combo.addItem(name, color)
        self.color_combo.currentIndexChanged.connect(self._choose_color)
        row.addWidget(self.color_combo)

        size_label = QLabel("Size")
        size_label.setObjectName("muted")
        row.addWidget(size_label)
        self.size_combo = QComboBox()
        self.size_combo.setObjectName("brushSizeCombo")
        for size in (2, 4, 6, 10, 14, 20):
            self.size_combo.addItem(f"{size} px", size)
        self.size_combo.setCurrentIndex(1)
        self.size_combo.currentIndexChanged.connect(self._choose_size)
        row.addWidget(self.size_combo)

        self.clear_button = QPushButton("Clear canvas")
        self.clear_button.setObjectName("clearCanvasButton")
        self.clear_button.clicked.connect(self.canvas.clear)
        row.addWidget(self.clear_button)
        row.addStretch()
        return frame

    def _choose_color(self, index: int) -> None:
        color = self.color_combo.itemData(index)
        if color:
            self.canvas.set_pen_color(color)

    def _choose_size(self, index: int) -> None:
        size = self.size_combo.itemData(index)
        if size:
            self.canvas.set_brush_size(size)

    def _canvas(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("canvasFrame")
        frame.setMinimumWidth(450)
        frame.setStyleSheet(
            "QFrame#canvasFrame { background-color: #ffffff; border: 1px solid #e2e7f0;"
            " border-radius: 16px; }"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.canvas)
        return frame

    def _sidebar(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(14)
        layout.addWidget(self._participants_card(), 1)
        layout.addWidget(self._chat_card(), 1)
        return layout

    def _participants_card(self) -> QFrame:
        card = self._sidebar_card("Participants", "Waiting for room…")
        self.participants_subtitle = card.layout().itemAt(1).widget()
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
        participant_id: str,
    ) -> None:
        """Populate room identity and reset the local view for a new room."""
        role = "Host" if is_host else "Participant"
        self.room_label.setText(f"Room · {room_code}  /  {role}")
        self.connection_label.setText(f"Connected · {server_address}:{server_port}")
        self.username = username
        self.participant_id = participant_id
        self.participant_list.clear()
        self.canvas.clear()

    def update_participants(self, participants: list[dict]) -> None:
        self.participant_list.clear()
        for participant in participants:
            is_self = participant.get("participant_id") == self.participant_id
            host_suffix = " · Host" if participant.get("is_host") else ""
            self_suffix = " · You" if is_self else ""
            self.participant_list.addItem(
                f"●  {participant.get('username', 'Participant')}{host_suffix}{self_suffix}"
            )
        count = len(participants)
        self.participants_subtitle.setText(
            f"{count} participant" + ("" if count == 1 else "s")
        )

    def set_connection_status(self, message: str) -> None:
        self.connection_label.setText(message)

    def add_remote_operation(self, operation: dict) -> bool:
        return self.canvas.add_remote_operation(operation)

    def confirm_local_operation(self, operation_id: str, sequence_number: int) -> None:
        self.canvas.confirm_local_operation(operation_id, sequence_number)
