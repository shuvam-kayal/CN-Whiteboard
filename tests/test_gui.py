import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QFrame
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from client.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_create_room_navigation_and_return(app) -> None:
    window = MainWindow()
    window.show()
    app.processEvents()

    assert window.pages.currentWidget() is window.welcome_page
    QTest.mouseClick(window.welcome_page.create_button, Qt.MouseButton.LeftButton)
    assert window.welcome_page.error_label.text() == "Enter your name to continue."

    window.welcome_page.username_input.setText("Jordan")
    QTest.mouseClick(window.welcome_page.create_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window.pages.currentWidget() is window.whiteboard_page
    assert "WAVE24" in window.whiteboard_page.room_label.text()
    assert window.whiteboard_page.participant_list.count() == 3
    assert window.whiteboard_page.findChild(QFrame, "canvas") is not None

    QTest.mouseClick(window.whiteboard_page.leave_button, Qt.MouseButton.LeftButton)
    assert window.pages.currentWidget() is window.welcome_page
    window.close()


def test_join_room_uses_entered_room_and_connection_settings(app) -> None:
    window = MainWindow()
    window.welcome_page.username_input.setText("Casey")
    window.welcome_page.room_code_input.setText("abc123")
    window.welcome_page.server_address_input.setText("192.168.1.20")
    window.welcome_page.port_input.setValue(5012)

    QTest.mouseClick(window.welcome_page.join_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window.pages.currentWidget() is window.whiteboard_page
    assert "ABC123" in window.whiteboard_page.room_label.text()
    assert "192.168.1.20:5012" in window.whiteboard_page.connection_label.text()
    assert "not connected" in window.whiteboard_page.connection_label.text()
    window.close()
