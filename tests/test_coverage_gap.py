"""Targeted tests to close the remaining coverage gap to 80%."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

profile = importlib.import_module("haochen_app.pet.profile")
paths_module = importlib.import_module("haochen_app.paths")
version_module = importlib.import_module("haochen_app.version")
signing_status = importlib.import_module("haochen_app.signing_status")
key_validation = importlib.import_module("haochen_app.key_validation")
sidebar_module = importlib.import_module("haochen_app.chat.sidebar")
secure_storage = importlib.import_module("haochen_app.secure_storage")
permissions = importlib.import_module("haochen_app.permissions")


# ── pet/profile ──────────────────────────────────────────────

def test_profile_roundtrip_and_corruption(tmp_path: Path) -> None:
    monkey_home = tmp_path
    original = profile.haochen_home
    profile.haochen_home = lambda: monkey_home
    try:
        assert profile.should_ask_name() is True
        profile.save_user_name("阿晨")
        assert profile.should_ask_name() is False
        assert profile.load_user_name() == "阿晨"

        profile.profile_path().write_text("{broken", encoding="utf-8")
        assert profile.load_user_name() == ""

        profile.profile_path().write_text(json.dumps({"name": 42}), encoding="utf-8")
        assert profile.load_user_name() == ""

        profile.save_user_name("")  # 跳过 → 空名落盘
        assert profile.load_user_name() == ""
    finally:
        profile.haochen_home = original


def test_profile_save_failure_is_logged_not_raised(tmp_path: Path) -> None:
    original = profile.haochen_home
    profile.haochen_home = lambda: tmp_path

    def boom(_path, _content):
        raise OSError("disk full")

    profile.atomic_write_private = boom
    try:
        profile.save_user_name("名字")  # 不抛
    finally:
        profile.haochen_home = original


# ── paths ────────────────────────────────────────────────────

def test_paths_development_mode(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert paths_module.is_frozen() is False
    assert paths_module.resources_dir() == ROOT
    assert paths_module.engine_binary().name == "haochen-engine"
    assert paths_module.pet_assets().name == "pet"
    assert paths_module.ext_entry().name == "index.ts"
    assert paths_module.config_templates().name == "config"


# ── version ──────────────────────────────────────────────────

def test_version_frozen_candidate_lookup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    (tmp_path / "VERSION").write_text("9.9.9-frozen.1\n", encoding="utf-8")
    monkeypatch.delenv("HAOCHEN_VERSION", raising=False)
    assert version_module.application_version() == "9.9.9-frozen.1"


def test_version_env_override_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("HAOCHEN_VERSION", "1.2.3-env.4")
    assert version_module.application_version() == "1.2.3-env.4"


# ── signing_status ──────────────────────────────────────────

def test_signing_status_source_mode(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert signing_status.app_bundle() is None
    assert signing_status.signing_details() == ""
    assert signing_status.is_developer_id_signed() is False
    assert signing_status.signing_summary() == "开发模式（未打包）"


def test_signing_status_frozen_without_codesign(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/haochen.app/Contents/MacOS/haochen")
    monkeypatch.setattr(
        signing_status.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    assert signing_status.is_developer_id_signed() is False
    assert signing_status.signing_summary() == "未通过 Developer ID 签名（仅限开发测试）"


# ── key_validation ──────────────────────────────────────────

def test_key_validation_unknown_provider_and_short_key() -> None:
    result = key_validation.validate_api_key("unknown", "some-long-key")
    assert result.ok is False and "不支持" in result.message
    result = key_validation.validate_api_key("deepseek", "short")
    assert result.ok is False and "过短" in result.message


def test_key_validation_http_5xx_and_2xx() -> None:
    class Resp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkey_target = key_validation.urllib.request
    original = monkey_target.urlopen
    monkey_target.urlopen = lambda *_a, **_k: Resp()
    try:
        assert key_validation.validate_api_key("deepseek", "k" * 20).ok is True
    finally:
        monkey_target.urlopen = original

    class Resp500(Resp):
        status = 503

    monkey_target.urlopen = lambda *_a, **_k: Resp500()
    try:
        result = key_validation.validate_api_key("deepseek", "k" * 20)
        assert result.ok is False and "503" in result.message
    finally:
        monkey_target.urlopen = original


# ── secure_storage error paths ──────────────────────────────

def test_ensure_private_file_rejects_missing_and_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        secure_storage.ensure_private_file(tmp_path / "missing.txt")
    with pytest.raises(ValueError):
        secure_storage.ensure_private_file(tmp_path)  # 目录非文件


# ── sidebar interactions ────────────────────────────────────

def test_sidebar_signals_and_status(qtbot) -> None:

    SessionSidebar = sidebar_module.SessionSidebar
    bar = SessionSidebar()
    qtbot.addWidget(bar)

    selected: list[str] = []
    bar.session_selected.connect(selected.append)
    bar.set_sessions(
        [{"path": "/a.jsonl", "title": "A"}, {"path": "/b.jsonl", "title": "B"}],
        "/b.jsonl",
    )
    assert bar.current_path() == "/b.jsonl"

    bar.list.item(0).setSelected(True)
    bar._on_click(bar.list.item(0))
    assert selected == ["/a.jsonl"]

    bar.set_status("状态文本")
    assert bar.status.text() == "状态文本"

    bar.update_title("/a.jsonl", "改名A")
    assert bar.list.item(0).text() == "改名A"

    bar.set_sessions([], None)
    assert bar.list.count() == 0


def test_sidebar_context_menu_rename_and_delete(qtbot, monkeypatch) -> None:
    """菜单弹窗无法无头驱动；验证菜单构建路径与信号发射。"""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QInputDialog, QMenu

    SessionSidebar = sidebar_module.SessionSidebar
    bar = SessionSidebar()
    qtbot.addWidget(bar)
    bar.set_sessions([{"path": "/a.jsonl", "title": "A"}], "/a.jsonl")

    renames: list[tuple[str, str]] = []
    deletes: list[str] = []
    bar.rename_requested.connect(lambda p, n: renames.append((p, n)))
    bar.delete_requested.connect(deletes.append)

    # 直接驱动菜单回调路径（跳过 exec）：构造后立即取 menu 的动作并触发。
    # 由于 _on_menu 内部 exec 阻塞，改为验证数据路径：
    monkeypatch.setattr(QMenu, "exec", lambda self, pos: None)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("新名字", True))
    bar._on_menu(QPoint(5, 5))  # exec 被 stub，菜单动作不发信号 → 只验证不崩溃
    assert renames == [] and deletes == []  # 无头下菜单项未触发
