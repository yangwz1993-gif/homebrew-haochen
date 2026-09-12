"""Diagnostic output validation only; normal unit tests never start native UI."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from haochen_app.dashboard.selfcheck import prepare_output  # noqa: E402


def test_selfcheck_creates_only_new_private_temp_directory(tmp_path):
    output = prepare_output(str(tmp_path / "new-diagnostic"))
    assert output.is_dir() and not list(output.iterdir())
    assert output.stat().st_mode & 0o777 == 0o700
    marker = output / "preserve.txt"
    marker.write_text("existing output belongs to the user")
    with pytest.raises(FileExistsError):
        prepare_output(str(output))
    assert marker.read_text() == "existing output belongs to the user"


def test_selfcheck_rejects_real_home_relative_traversal_and_links(tmp_path):
    for raw in (
        str(Path.home() / "haochen-diagnostic-must-not-exist"),
        "relative-diagnostic",
        "/tmp/../Users/diagnostic",
        "/tmp",
    ):
        with pytest.raises(ValueError):
            prepare_output(raw)
    link = tmp_path / "home-link"
    link.symlink_to(Path.home(), target_is_directory=True)
    with pytest.raises(ValueError):
        prepare_output(str(link / "diagnostic-must-not-exist"))
    destination = tmp_path / "existing-dir"
    destination.mkdir()
    symlink_output = tmp_path / "output-link"
    symlink_output.symlink_to(destination, target_is_directory=True)
    with pytest.raises(FileExistsError):
        prepare_output(str(symlink_output))
    assert symlink_output.is_symlink() and not list(destination.iterdir())


def test_selfcheck_dispatch_precedes_normal_app_imports_and_file_logging():
    source = (ROOT / "app/run_app.py").read_text()
    dispatch = source.index('if "--dashboard-check" in sys.argv:')
    assert dispatch < source.index("from haochen_app.app_shell import AppShell")
    assert dispatch < source.index("def _setup_file_logging")
