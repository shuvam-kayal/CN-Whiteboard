from pathlib import Path

import run_server


ROOT = Path(__file__).resolve().parents[1]


def test_project_directories_exist() -> None:
    for relative_path in (
        "client",
        "server",
        "shared",
        "tests",
        "docs",
        "data/boards",
    ):
        assert (ROOT / relative_path).is_dir()


def test_requirements_include_approved_gui_and_test_tools() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "PySide6" in requirements
    assert "pytest" in requirements


def test_server_entry_point_exposes_configurable_tcp_options() -> None:
    import inspect

    source = inspect.getsource(run_server.main)
    assert "--host" in source
    assert "--port" in source
