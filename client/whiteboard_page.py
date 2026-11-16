"""Whiteboard layout, local canvas, and room participant display."""

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QColorDialog,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from client.drawing_canvas import DrawingCanvas


class WhiteboardPage(QWidget):
    leave_requested = Signal()
    drawing_operation_completed = Signal(str, object)
    cursor_position_changed = Signal(float, float)
    chat_message_requested = Signal(str)
    save_board_requested = Signal()
    load_board_requested = Signal()
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 24)
        root.setSpacing(16)
        self.canvas = DrawingCanvas()
        self.canvas.operation_completed.connect(self.drawing_operation_completed.emit)
        self.canvas.cursor_moved.connect(self._queue_cursor_position)
        self.canvas.view_changed.connect(self._update_zoom_label)
        self._pending_cursor: tuple[float, float] | None = None
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(50)
        self._cursor_timer.timeout.connect(self._send_pending_cursor)
        self._known_participants: dict[str, str] = {}
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
        self.room_label.setStyleSheet("font-size: 15px; font-weight: 700; color: #3f47c2;")
        title_stack.addWidget(title)
        title_stack.addWidget(self.room_label)
        row.addLayout(title_stack)
        self.copy_room_code_button = QPushButton("Copy code")
        self.copy_room_code_button.setObjectName("copyRoomCodeButton")
        self.copy_room_code_button.setToolTip("Copy this room code to share with other participants")
        self.copy_room_code_button.clicked.connect(self._copy_room_code)
        row.addWidget(self.copy_room_code_button)
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
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)
        row = QHBoxLayout()
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
        self.pen_button.clicked.connect(lambda: self.shape_combo.setCurrentIndex(0))
        self.eraser_button.clicked.connect(lambda: self.shape_combo.setCurrentIndex(0))
        row.addWidget(self.pen_button)
        row.addWidget(self.eraser_button)

        self.shape_combo = QComboBox()
        self.shape_combo.setObjectName("shapeToolCombo")
        self.shape_combo.addItem("Shape…", None)
        self.shape_combo.addItem("Line", "line")
        self.shape_combo.addItem("Rectangle", "rectangle")
        self.shape_combo.addItem("Ellipse", "ellipse")
        self.shape_combo.currentIndexChanged.connect(self._choose_shape)
        row.addWidget(self.shape_combo)

        self.brush_combo = QComboBox()
        self.brush_combo.setObjectName("brushTypeCombo")
        for label, brush in (("Pen", "pen"), ("Pencil", "pencil"), ("Marker", "marker"), ("Highlighter", "highlighter")):
            self.brush_combo.addItem(label, brush)
        self.brush_combo.currentIndexChanged.connect(self._choose_brush)
        row.addWidget(self.brush_combo)

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
            ("Black", "#000000"),
            ("White", "#ffffff"),
            ("Red", "#e53935"),
            ("Yellow", "#fdd835"),
            ("Purple", "#8e24aa"),
            ("Pink", "#ec407a"),
            ("Cyan", "#00acc1"),
            ("Brown", "#795548"),
            ("Gray", "#757575"),
        ):
            self.color_combo.addItem(name, color)
        self.color_combo.currentIndexChanged.connect(self._choose_color)
        row.addWidget(self.color_combo)
        self.color_swatch = QLabel("●")
        self.color_swatch.setObjectName("selectedColorSwatch")
        self.color_swatch.setToolTip("Currently selected drawing colour")
        self.color_swatch.setStyleSheet(
            f"color: {self.canvas.pen_color}; font-size: 22px;"
        )
        row.addWidget(self.color_swatch)
        self.custom_color_button = QPushButton("Custom…")
        self.custom_color_button.setObjectName("customColorButton")
        self.custom_color_button.clicked.connect(self._pick_custom_color)
        row.addWidget(self.custom_color_button)

        size_label = QLabel("Size")
        size_label.setObjectName("muted")
        row.addWidget(size_label)
        self.size_combo = QComboBox()
        self.size_combo.setObjectName("brushSizeCombo")
        for size in (2, 4, 6, 10, 14, 20, 28, 36, 48, 64, 96, 128):
            self.size_combo.addItem(f"{size} px", size)
        self.size_combo.setCurrentIndex(1)
        self.size_combo.currentIndexChanged.connect(self._choose_size)
        row.addWidget(self.size_combo)
        row.addStretch()
        layout.addLayout(row)

        actions = QHBoxLayout()
        actions.setContentsMargins(12, 0, 12, 9)
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setObjectName("zoomOutButton")
        self.zoom_out_button.setToolTip("Zoom out")
        self.zoom_out_button.clicked.connect(self.canvas.zoom_out)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("zoomPercentageLabel")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setObjectName("zoomInButton")
        self.zoom_in_button.setToolTip("Zoom in")
        self.zoom_in_button.clicked.connect(self.canvas.zoom_in)
        self.reset_view_button = QPushButton("Reset view")
        self.reset_view_button.setObjectName("resetViewButton")
        self.reset_view_button.clicked.connect(self.canvas.reset_view)
        actions.addWidget(self.zoom_out_button)
        actions.addWidget(self.zoom_label)
        actions.addWidget(self.zoom_in_button)
        actions.addWidget(self.reset_view_button)
        actions.addStretch()

        self.clear_button = QPushButton("Clear canvas")
        self.clear_button.setObjectName("clearCanvasButton")
        self.clear_button.clicked.connect(self.canvas.clear)
        actions.addWidget(self.clear_button)
        self.save_board_button = QPushButton("Save board")
        self.save_board_button.setObjectName("saveBoardButton")
        self.save_board_button.clicked.connect(self.save_board_requested.emit)
        self.load_board_button = QPushButton("Load board")
        self.load_board_button.setObjectName("loadBoardButton")
        self.load_board_button.clicked.connect(self.load_board_requested.emit)
        actions.addWidget(self.save_board_button)
        actions.addWidget(self.load_board_button)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setObjectName("undoButton")
        self.undo_button.clicked.connect(self.undo_requested.emit)
        self.redo_button = QPushButton("Redo")
        self.redo_button.setObjectName("redoButton")
        self.redo_button.clicked.connect(self.redo_requested.emit)
        actions.addWidget(self.undo_button)
        actions.addWidget(self.redo_button)
        self.export_button = QPushButton("Export PNG")
        self.export_button.setObjectName("exportPngButton")
        self.export_button.clicked.connect(self._export_png)
        actions.addWidget(self.export_button)
        layout.addLayout(actions)
        return frame

    def _choose_shape(self, index: int) -> None:
        shape = self.shape_combo.itemData(index)
        if not shape:
            return
        self.tool_group.setExclusive(False)
        self.pen_button.setChecked(False)
        self.eraser_button.setChecked(False)
        self.tool_group.setExclusive(True)
        self.canvas.set_tool(shape)

    def _export_png(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self, "Export whiteboard as PNG", "whiteboard.png", "PNG image (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            exported = self.canvas.export_png(path)
        except (ValueError, MemoryError) as exc:
            self.connection_label.setText(f"PNG export failed · {exc}")
            self.connection_label.setStyleSheet("color: #b53c4b;")
            return
        if exported:
            self.connection_label.setText(f"Exported · {path}")
        else:
            self.connection_label.setText("PNG export failed · could not write file")
            self.connection_label.setStyleSheet("color: #b53c4b;")

    def _choose_color(self, index: int) -> None:
        color = self.color_combo.itemData(index)
        if color:
            self.set_selected_color(color)

    def set_selected_color(self, color: str) -> None:
        """Set a valid custom or preset color and keep the toolbar indicator current."""
        parsed = QColor(color)
        if not parsed.isValid():
            raise ValueError("color must be a valid Qt color")
        normalized = parsed.name(QColor.NameFormat.HexRgb)
        self.canvas.set_pen_color(normalized)
        self.color_swatch.setStyleSheet(f"color: {normalized}; font-size: 22px;")
        self.color_swatch.setText("●")
        match = self.color_combo.findData(normalized)
        if match >= 0 and self.color_combo.currentIndex() != match:
            self.color_combo.setCurrentIndex(match)
        elif match < 0:
            self.color_combo.setCurrentIndex(-1)
            self.color_combo.setToolTip(f"Custom colour {normalized}")

    def _pick_custom_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.canvas.pen_color), self, "Choose drawing colour")
        if color.isValid():
            self.set_selected_color(color.name(QColor.NameFormat.HexRgb))

    def _choose_brush(self, index: int) -> None:
        brush = self.brush_combo.itemData(index)
        if brush:
            self.canvas.set_brush_type(brush)

    def _choose_size(self, index: int) -> None:
        size = self.size_combo.itemData(index)
        if size:
            self.canvas.set_brush_size(size)

    def _update_zoom_label(self, zoom: float) -> None:
        self.zoom_label.setText(f"{round(zoom * 100)}%")

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
        self.participant_activity = QLabel("")
        self.participant_activity.setObjectName("muted")
        self.participant_activity.setStyleSheet("font-size: 11px; color: #5b63e8;")
        self.participant_activity.hide()
        self._activity_timer = QTimer(self)
        self._activity_timer.setSingleShot(True)
        self._activity_timer.setInterval(4000)
        self._activity_timer.timeout.connect(self.participant_activity.hide)
        card.layout().addWidget(self.participant_activity)
        self.participant_list = QListWidget()
        self.participant_list.setObjectName("participantList")
        self.participant_list.setMinimumWidth(250)
        card.layout().addWidget(self.participant_list, 1)
        return card

    def _chat_card(self) -> QFrame:
        card = self._sidebar_card("Room chat", "Messages for this room")
        self.chat_history = QTextBrowser()
        self.chat_history.setObjectName("chatHistory")
        self.chat_history.setOpenLinks(False)
        self.chat_history.setStyleSheet("background: transparent; border: none;")
        card.layout().addWidget(self.chat_history, 1)

        self.chat_status = QLabel("")
        self.chat_status.setObjectName("muted")
        self.chat_status.setStyleSheet("font-size: 11px; color: #b53c4b;")
        card.layout().addWidget(self.chat_status)

        send_row = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setObjectName("chatInput")
        self.chat_input.setPlaceholderText("Write a message…")
        self.chat_input.setMaxLength(2000)
        self.chat_send_button = QPushButton("Send")
        self.chat_send_button.setObjectName("chatSendButton")
        self.chat_send_button.clicked.connect(self._send_chat_message)
        self.chat_input.returnPressed.connect(self._send_chat_message)
        send_row.addWidget(self.chat_input, 1)
        send_row.addWidget(self.chat_send_button)
        card.layout().addLayout(send_row)
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
        self.room_code = room_code
        self.room_label.setText(f"Room · {room_code}  /  {role}")
        self.copy_room_code_button.setText("Copy code")
        self.connection_label.setText(f"Connected · {server_address}:{server_port}")
        self.username = username
        self.participant_id = participant_id
        self.is_host = is_host
        self.save_board_button.setEnabled(is_host)
        self.load_board_button.setEnabled(is_host)
        self.participant_list.clear()
        self._known_participants.clear()
        self.participant_activity.hide()
        self.canvas.remote_cursors.clear()
        self.canvas.clear()
        self.canvas.reset_view()
        self.chat_history.clear()
        self.chat_status.clear()
        self.persistence_status = ""

    def _copy_room_code(self) -> None:
        if not isinstance(getattr(self, "room_code", None), str):
            return
        QApplication.clipboard().setText(self.room_code)
        self.copy_room_code_button.setText("Copied!")
        QTimer.singleShot(1600, lambda: self.copy_room_code_button.setText("Copy code"))

    def update_participants(self, participants: list[dict]) -> None:
        current = {
            participant["participant_id"]: participant["username"]
            for participant in participants
            if isinstance(participant, dict)
            and isinstance(participant.get("participant_id"), str)
            and isinstance(participant.get("username"), str)
        }
        if self._known_participants:
            joined = [name for key, name in current.items() if key not in self._known_participants]
            left = [name for key, name in self._known_participants.items() if key not in current]
            notices = []
            if joined:
                notices.append(f"{', '.join(joined)} joined")
            if left:
                notices.append(f"{', '.join(left)} left")
            if notices:
                self.participant_activity.setText(" · ".join(notices))
                self.participant_activity.show()
                self._activity_timer.start()

        self._known_participants = current
        self.participant_list.clear()
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            is_self = participant.get("participant_id") == self.participant_id
            host_suffix = " · Host" if participant.get("is_host") else ""
            self_suffix = " · You" if is_self else ""
            item = QListWidgetItem(
                f"●  {participant.get('username', 'Participant')}{host_suffix}{self_suffix}"
            )
            color = participant.get("cursor_color")
            if isinstance(color, str) and QColor(color).isValid():
                item.setForeground(QColor(color))
            self.participant_list.addItem(item)
        count = len(participants)
        self.participants_subtitle.setText(
            f"{count} participant" + ("" if count == 1 else "s")
        )
        self.canvas.remove_remote_cursors(set(current))

    def set_connection_status(self, message: str) -> None:
        self.connection_label.setText(message)

    def set_host_status(self, is_host: bool) -> None:
        self.is_host = is_host
        self.save_board_button.setEnabled(is_host)
        self.load_board_button.setEnabled(is_host)

    def add_remote_operation(self, operation: dict) -> bool:
        return self.canvas.add_remote_operation(operation)

    def replace_board_state(self, operations: object) -> None:
        self.canvas.replace_board_state(operations)

    def set_operation_active(self, sequence_number: int, active: bool) -> bool:
        return self.canvas.set_operation_active(sequence_number, active)

    def set_persistence_status(self, message: str, *, is_error: bool = False) -> None:
        self.persistence_status = message
        color = "#b53c4b" if is_error else "#318064"
        self.connection_label.setStyleSheet(f"color: {color};")
        self.connection_label.setText(message)

    def confirm_local_operation(self, operation_id: str, sequence_number: int) -> None:
        self.canvas.confirm_local_operation(operation_id, sequence_number)

    def set_remote_cursor(self, cursor: dict) -> None:
        self.canvas.set_remote_cursor(cursor)

    def append_chat_message(self, message: dict) -> None:
        """Append one server-authored room message in receive order."""
        import html

        username = html.escape(str(message.get("username", "Participant")))
        timestamp = html.escape(str(message.get("timestamp", "")))
        text = html.escape(str(message.get("text", ""))).replace("\n", "<br>")
        self.chat_history.append(
            f'<span style="color:#738099;font-size:11px"><b>{username}</b> · '
            f"{timestamp}</span><br>{text}"
        )
        self.chat_status.clear()

    def show_chat_status(self, message: str) -> None:
        self.chat_status.setText(message)

    def _send_chat_message(self) -> None:
        text = self.chat_input.text().strip()
        if not text:
            self.show_chat_status("Enter a message before sending.")
            return
        self.chat_input.clear()
        self.chat_status.clear()
        self.chat_message_requested.emit(text)

    def _queue_cursor_position(self, x: float, y: float) -> None:
        self._pending_cursor = (x, y)
        if not self._cursor_timer.isActive():
            self._cursor_timer.start()

    def _send_pending_cursor(self) -> None:
        if self._pending_cursor is not None:
            x, y = self._pending_cursor
            self._pending_cursor = None
            self.cursor_position_changed.emit(x, y)
