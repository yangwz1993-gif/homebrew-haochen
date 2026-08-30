from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

storage = importlib.import_module("haochen_app.secure_storage")
tracking = importlib.import_module("haochen_app.app_tracking")
config_store = importlib.import_module("haochen_app.settings.config_store")
engine_client = importlib.import_module("haochen_app.engine_client")


def mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_private_directory_repairs_existing_permissions(tmp_path: Path) -> None:
    directory = tmp_path / "data"
    directory.mkdir(mode=0o755)
    storage.ensure_private_directory(directory)
    assert mode(directory) == 0o700


def test_private_directory_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        storage.ensure_private_directory(link)


def test_atomic_private_write_uses_0600_and_replaces_content(tmp_path: Path) -> None:
    path = tmp_path / "private" / "value.json"
    storage.atomic_write_private(path, "first")
    storage.atomic_write_private(path, "second")
    assert path.read_text(encoding="utf-8") == "second"
    assert mode(path.parent) == 0o700
    assert mode(path) == 0o600
    assert not list(path.parent.glob(".*.tmp"))


def test_config_store_repairs_directory_and_file_modes(tmp_path: Path, monkeypatch) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "models.json").write_text('{"providers": {}}', encoding="utf-8")
    (templates / "settings.json").write_text('{}', encoding="utf-8")
    (templates / "auth.json.template").write_text('{}', encoding="utf-8")
    monkeypatch.setattr(config_store, "TEMPLATE_DIR", templates)

    store = config_store.ConfigStore(tmp_path / "home")
    created = store.ensure_initialized()

    assert mode(store.home) == 0o700
    assert mode(store.agent_dir) == 0o700
    assert {mode(path) for path in created} == {0o600}
    store.set_key("provider", "test-value-that-is-not-real")
    assert mode(store.agent_dir / "auth.json") == 0o600


def test_engine_paths_are_private_and_child_uses_restrictive_umask(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    engine_client.spawn_argv(Path("/bin/true"), None, home)
    assert mode(home) == 0o700
    assert {mode(home / name) for name in ("agent", "pi-home", "pi-sessions", "logs")} == {0o700}

    captured = {}

    class FakeProcess:
        stdout = []
        stdin = None

    monkeypatch.setattr(
        engine_client.subprocess,
        "Popen",
        lambda *_args, **kwargs: captured.update(kwargs) or FakeProcess(),
    )
    monkeypatch.setattr(engine_client.threading.Thread, "start", lambda _self: None)
    client = engine_client.EngineClient(engine=Path("/bin/true"), ext=None, home=home)
    client.start()
    assert captured["umask"] == 0o077


def test_user_text_is_reduced_to_boolean_intent_not_written_in_plaintext(tmp_path: Path) -> None:
    home = tmp_path / "home"
    tracking.write_last_user_text(home, "请看看这张图里的人是谁，private words")

    intent_path = home / "last-user-intent.json"
    assert json.loads(intent_path.read_text(encoding="utf-8")) == {"visual": True}
    assert "private words" not in intent_path.read_text(encoding="utf-8")
    assert not (home / "last-user-text.txt").exists()
    assert mode(home) == 0o700
    assert mode(intent_path) == 0o600


def test_non_visual_user_text_leaves_only_false_intent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    legacy = home / "last-user-text.txt"
    home.mkdir()
    legacy.write_text("legacy secret", encoding="utf-8")

    tracking.write_last_user_text(home, "帮我解释这段代码")

    assert json.loads((home / "last-user-intent.json").read_text(encoding="utf-8")) == {"visual": False}
    assert not legacy.exists()
    assert os.listdir(home) == ["last-user-intent.json"]
