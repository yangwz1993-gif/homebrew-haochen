from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

engine_client = importlib.import_module("haochen_app.engine_client")
DeletedSession = engine_client.DeletedSession
delete_session = engine_client.delete_session
restore_session = engine_client.restore_session


def make_session(home: Path, name: str = "session.jsonl") -> Path:
    sessions = home / "pi-sessions"
    sessions.mkdir(parents=True, mode=0o700)
    path = sessions / name
    path.write_text('{"type":"session","id":"test"}\n', encoding="utf-8")
    return path


def test_delete_moves_valid_session_to_private_recycle_bin(tmp_path: Path) -> None:
    home = tmp_path / "home"
    session = make_session(home)

    deletion = delete_session(str(session), home)

    assert isinstance(deletion, DeletedSession)
    assert deletion.original_path == session.resolve()
    assert not session.exists()
    assert deletion.recycled_path.is_file()
    assert deletion.recycled_path.parent == (home / "session-recycle-bin").resolve()
    assert deletion.recycled_path.stat().st_mode & 0o077 == 0


def test_restore_returns_session_without_overwriting(tmp_path: Path) -> None:
    home = tmp_path / "home"
    session = make_session(home)
    deletion = delete_session(str(session), home)

    restored = restore_session(deletion, home)

    assert restored == session.resolve()
    assert restored.is_file()
    assert not deletion.recycled_path.exists()
    restored.write_text("replacement", encoding="utf-8")
    deletion.recycled_path.write_text("deleted", encoding="utf-8")
    with pytest.raises(FileExistsError):
        restore_session(deletion, home)
    assert restored.read_text(encoding="utf-8") == "replacement"


@pytest.mark.parametrize(
    "candidate",
    [
        "../outside.jsonl",
        "nested/session.jsonl",
        "session.txt",
    ],
)
def test_delete_rejects_invalid_relative_paths(tmp_path: Path, candidate: str) -> None:
    home = tmp_path / "home"
    make_session(home)
    with pytest.raises(ValueError):
        delete_session(candidate, home)


def test_delete_rejects_absolute_path_outside_session_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside.jsonl"
    outside.write_text("do not delete", encoding="utf-8")

    with pytest.raises(ValueError):
        delete_session(str(outside), home)

    assert outside.read_text(encoding="utf-8") == "do not delete"


def test_delete_rejects_lexical_traversal_even_if_it_resolves_inside(tmp_path: Path) -> None:
    home = tmp_path / "home"
    session = make_session(home)
    traversing = session.parent / ".." / "pi-sessions" / session.name

    with pytest.raises(ValueError):
        delete_session(str(traversing), home)

    assert session.is_file()


def test_delete_rejects_symlink_even_when_target_is_inside_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = make_session(home, "target.jsonl")
    link = target.parent / "link.jsonl"
    os.symlink(target, link)

    with pytest.raises(ValueError):
        delete_session(str(link), home)

    assert target.is_file()
    assert link.is_symlink()


def test_delete_missing_session_is_visible_failure(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "pi-sessions").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        delete_session(str(home / "pi-sessions" / "missing.jsonl"), home)
