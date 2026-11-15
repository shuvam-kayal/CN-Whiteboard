import os
import socket
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from client.main_window import MainWindow
from server.tcp_server import TCPServer
from shared.protocol import MessageBuffer, encode_message


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def server():
    tcp_server = TCPServer(host="127.0.0.1", port=0)
    thread = threading.Thread(target=tcp_server.serve_forever, daemon=True)
    thread.start()
    assert tcp_server.wait_until_ready(3)
    yield tcp_server
    tcp_server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def wait_until(app, predicate, timeout=3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "GUI did not reach the expected state"


def create_raw_room(address: tuple[str, int]) -> tuple[socket.socket, str]:
    connection = socket.create_connection(address, timeout=2)
    connection.sendall(encode_message("create_room", "host-create", {"username": "Host"}))
    buffer = MessageBuffer()
    messages = []
    while not messages:
        messages.extend(buffer.feed(connection.recv(4096)))
    room_message = next(message for message in messages if message["type"] == "room_created")
    room_code = room_message["payload"]["room_code"]
    return connection, room_code


def test_create_room_navigation_and_return(app, server) -> None:
    window = MainWindow()
    window.show()
    app.processEvents()

    assert window.pages.currentWidget() is window.welcome_page
    QTest.mouseClick(window.welcome_page.create_button, Qt.MouseButton.LeftButton)
    assert window.welcome_page.error_label.text() == "Enter your name to continue."

    window.welcome_page.username_input.setText("Jordan")
    window.welcome_page.server_address_input.setText(server.server_address[0])
    window.welcome_page.port_input.setValue(server.server_address[1])
    QTest.mouseClick(window.welcome_page.create_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: window.pages.currentWidget() is window.whiteboard_page)
    assert window.pages.currentWidget() is window.whiteboard_page
    assert "Room · " in window.whiteboard_page.room_label.text()
    assert window.whiteboard_page.participant_list.count() == 1
    assert window.whiteboard_page.canvas is not None

    QTest.mouseClick(window.whiteboard_page.leave_button, Qt.MouseButton.LeftButton)
    assert window.pages.currentWidget() is window.welcome_page
    wait_until(app, lambda: server.sessions.room_count == 0)
    window.close()


def test_join_room_uses_entered_room_and_connection_settings(app, server) -> None:
    host, room_code = create_raw_room(server.server_address)
    window = MainWindow()
    window.welcome_page.username_input.setText("Casey")
    window.welcome_page.room_code_input.setText(room_code.lower())
    window.welcome_page.server_address_input.setText(server.server_address[0])
    window.welcome_page.port_input.setValue(server.server_address[1])

    QTest.mouseClick(window.welcome_page.join_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: window.pages.currentWidget() is window.whiteboard_page)
    assert window.pages.currentWidget() is window.whiteboard_page
    assert room_code in window.whiteboard_page.room_label.text()
    assert f"{server.server_address[0]}:{server.server_address[1]}" in (
        window.whiteboard_page.connection_label.text()
    )
    assert window.whiteboard_page.participant_list.count() == 2
    window.close()
    host.close()


def test_toolbar_controls_set_pen_color_size_tool_and_clear(app) -> None:
    window = MainWindow()
    board = window.whiteboard_page
    board.color_combo.setCurrentIndex(2)
    board.size_combo.setCurrentIndex(4)
    QTest.mouseClick(board.eraser_button, Qt.MouseButton.LeftButton)
    app.processEvents()

    assert board.canvas.pen_color == "#e05a5a"
    assert board.canvas.brush_size == 14
    assert board.canvas.tool == "eraser"
    board.canvas.engine.begin_stroke((10, 10), "#ffffff", 14, "eraser")
    board.canvas.engine.finish_stroke((20, 20))
    QTest.mouseClick(board.clear_button, Qt.MouseButton.LeftButton)
    assert board.canvas.engine.operations == []
    window.close()
