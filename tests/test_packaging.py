"""Data-path and safe-default checks for source and frozen applications."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from client.welcome_page import WelcomePage
from server.persistence import PersistenceManager
from shared import app_paths


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_source_mode_keeps_board_data_inside_project(monkeypatch):
    monkeypatch.setattr(app_paths.sys, "frozen", False, raising=False)
    assert app_paths.app_data_directory() == Path(__file__).resolve().parents[1]
    assert app_paths.boards_directory() == Path(__file__).resolve().parents[1] / "data" / "boards"


def test_frozen_mode_uses_stable_local_app_data_and_persists_across_manager_instances(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app_paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    expected_root = tmp_path / "LANWhiteboard"
    assert app_paths.app_data_directory() == expected_root
    assert app_paths.logs_directory() == expected_root / "logs"

    operation = {
        "operation_type": "pen", "points": [[-12, 34]], "color": "#123456",
        "brush_size": 4, "sequence_number": 1, "participant_id": "member",
        "room_code": "ABCD23", "active": True,
    }
    writer = PersistenceManager()
    writer.save_board("ABCD23", [operation])
    assert writer.boards_directory == expected_root / "data" / "boards"
    reader = PersistenceManager()
    assert reader.load_board("ABCD23").operations[0]["points"] == [[-12, 34]]
    assert (expected_root / "data" / "boards" / "ABCD23.json").is_file()


def test_client_requires_explicit_server_address_to_avoid_localhost_confusion(app):
    welcome = WelcomePage()
    assert welcome.server_address_input.text() == ""
    welcome.close()


def test_client_packaging_filters_host_specific_icu_dlls():
    spec = Path(__file__).resolve().parents[1] / "WhiteboardClient.spec"
    source = spec.read_text(encoding="utf-8")
    assert '"icuuc.dll"' in source
    assert '"icudt78.dll"' in source
