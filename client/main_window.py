"""Minimal desktop window for the initial project slice."""

from PySide6.QtWidgets import QLabel, QMainWindow


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LAN Collaborative Whiteboard")
        self.resize(900, 600)
        self.setCentralWidget(QLabel("Whiteboard foundation is ready."))
