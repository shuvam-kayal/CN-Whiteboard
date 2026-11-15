import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from client.main_window import MainWindow
from server.tcp_server import TCPServer


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
    assert tcp_server.server_address is not None
    yield tcp_server
    tcp_server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()


def wait_until(app, predicate, timeout: float = 4) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "collaborative GUI state was not reached"


def test_remote_strokes_render_on_other_room_members_in_gui(app, server) -> None:
    address, port = server.server_address
    alice = MainWindow()
    bob = MainWindow()
    alice.show()
    bob.show()
    alice.welcome_page.username_input.setText("Alice")
    alice.welcome_page.server_address_input.setText(address)
    alice.welcome_page.port_input.setValue(port)
    QTest.mouseClick(alice.welcome_page.create_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: alice.pages.currentWidget() is alice.whiteboard_page)

    bob.welcome_page.username_input.setText("Bob")
    bob.welcome_page.server_address_input.setText(address)
    bob.welcome_page.port_input.setValue(port)
    bob.welcome_page.room_code_input.setText(alice.room_code)
    QTest.mouseClick(bob.welcome_page.join_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: bob.pages.currentWidget() is bob.whiteboard_page)
    wait_until(app, lambda: alice.whiteboard_page.participant_list.count() == 2)

    gui_thread = app.thread()
    remote_update_threads = []
    add_remote = bob.whiteboard_page.add_remote_operation
    set_remote_cursor = bob.whiteboard_page.set_remote_cursor
    append_chat_message = bob.whiteboard_page.append_chat_message

    def record_gui_thread(payload):
        remote_update_threads.append(QThread.currentThread() == gui_thread)
        return add_remote(payload)

    def record_cursor_gui_thread(payload):
        remote_update_threads.append(QThread.currentThread() == gui_thread)
        return set_remote_cursor(payload)

    def record_chat_gui_thread(payload):
        remote_update_threads.append(QThread.currentThread() == gui_thread)
        return append_chat_message(payload)

    bob.whiteboard_page.add_remote_operation = record_gui_thread
    bob.whiteboard_page.set_remote_cursor = record_cursor_gui_thread
    bob.whiteboard_page.append_chat_message = record_chat_gui_thread

    canvas = alice.whiteboard_page.canvas
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(45, 65))
    QTest.mouseMove(canvas, QPoint(140, 105), delay=10)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(140, 105))
    wait_until(app, lambda: len(bob.whiteboard_page.canvas.engine.operations) == 1)
    wait_until(app, lambda: alice.whiteboard_page.canvas.engine.operations[0].sequence_number == 1)

    alice_operations = alice.whiteboard_page.canvas.engine.operations
    bob_operations = bob.whiteboard_page.canvas.engine.operations
    assert len(alice_operations) == 1  # The server does not echo to the sender.
    assert len(bob_operations) == 1
    assert bob_operations[0].sequence_number == 1
    assert bob_operations[0].points == alice_operations[0].points
    assert remote_update_threads and all(remote_update_threads)
    image = bob.whiteboard_page.canvas.grab().toImage()
    color_pixels = [
        image.pixelColor(x, y).name()
        for x in range(30, min(160, image.width()))
        for y in range(50, min(120, image.height()))
        if image.pixelColor(x, y).name() == alice_operations[0].color
    ]
    assert color_pixels

    operation_count = len(bob.whiteboard_page.canvas.engine.operations)
    canvas.cursor_moved.emit(0.72, 0.33)
    wait_until(
        app,
        lambda: alice.participant_id in bob.whiteboard_page.canvas.remote_cursors,
    )
    remote_cursor = bob.whiteboard_page.canvas.remote_cursors[alice.participant_id]
    assert remote_cursor["x"] == 0.72
    assert remote_cursor["y"] == 0.33
    assert remote_cursor["color"] == "#e4572e"
    assert len(bob.whiteboard_page.canvas.engine.operations) == operation_count
    assert all(remote_update_threads)

    alice.whiteboard_page.chat_input.setText("First message from Alice")
    QTest.mouseClick(
        alice.whiteboard_page.chat_send_button, Qt.MouseButton.LeftButton
    )
    wait_until(
        app,
        lambda: "First message from Alice"
        in bob.whiteboard_page.chat_history.toPlainText(),
    )
    alice.whiteboard_page.chat_input.setText("Second message from Alice")
    QTest.mouseClick(
        alice.whiteboard_page.chat_send_button, Qt.MouseButton.LeftButton
    )
    wait_until(
        app,
        lambda: "Second message from Alice"
        in bob.whiteboard_page.chat_history.toPlainText(),
    )
    for page in (alice.whiteboard_page, bob.whiteboard_page):
        history = page.chat_history.toPlainText()
        assert history.index("First message from Alice") < history.index(
            "Second message from Alice"
        )
        assert "Alice" in history
    assert all(remote_update_threads)

    alice.close()
    wait_until(app, lambda: bob.whiteboard_page.participant_list.count() == 1)
    assert "Alice left" in bob.whiteboard_page.participant_activity.text()
    assert alice.participant_id not in bob.whiteboard_page.canvas.remote_cursors

    bob.close()
