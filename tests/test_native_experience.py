"""Menu bar, keyboard shortcuts, reduce-motion, screen placement and window memory (task-4c)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QApplication, QMenuBar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import test_session_deletion_ui as harness  # noqa: E402

FakeClient = harness.FakeClient
window_module = harness.window_module
ChatWindow = harness.ChatWindow
a11y = importlib.import_module("haochen_app.a11y")
widgets_module = importlib.import_module("haochen_app.chat.widgets")
menu_module = importlib.import_module("haochen_app.menu_bar")


def test_menu_bar_provides_required_native_entries(qtbot) -> None:
    client = FakeClient()
    window = ChatWindow(client)
    qtbot.addWidget(window)

    # 清理其它测试留下的全局菜单栏（生产只装一次，测试间需隔离）。
    app = QApplication.instance()
    existing = getattr(app, "_haochen_menu_bar", None)
    if existing is not None:
        existing.deleteLater()
        app._haochen_menu_bar = None

    calls: list[str] = []
    shell = SimpleNamespace(
        show_chat=lambda: calls.append("chat"),
        show_settings=lambda: calls.append("settings"),
        new_session=lambda: calls.append("new"),
        chat=SimpleNamespace(_new_session=lambda: calls.append("new")),
    )
    bar = menu_module.install_menu_bar(QApplication.instance(), shell)
    assert isinstance(bar, QMenuBar)

    def find(text: str) -> QAction:
        for action in bar.actions():
            menu = action.menu()
            if menu is None:
                continue
            for entry in menu.actions():
                if entry.text() == text:
                    assert entry is not None
                    return entry
        raise AssertionError(f"menu entry missing: {text}")

    for label in ("打开对话", "设置…", "新会话", "关于 haochen", "退出 haochen"):
        find(label)

    assert find("打开对话").shortcut().toString() == "Ctrl+1"
    assert find("打开对话").shortcutContext() == Qt.ShortcutContext.ApplicationShortcut
    assert find("设置…").menuRole() == QAction.MenuRole.PreferencesRole
    assert find("设置…").shortcut() == menu_module._standard_shortcut(
        QKeySequence.StandardKey.Preferences, "Ctrl+,"
    )
    assert find("新会话").shortcut() == menu_module._standard_shortcut(
        QKeySequence.StandardKey.New, "Ctrl+N"
    )
    assert find("退出 haochen").shortcut() == menu_module._standard_shortcut(
        QKeySequence.StandardKey.Quit, "Ctrl+Q"
    )
    for label in ("设置…", "新会话", "退出 haochen"):
        assert find(label).shortcutContext() == Qt.ShortcutContext.ApplicationShortcut

    find("打开对话").trigger()
    find("设置…").trigger()
    find("新会话").trigger()
    assert calls == ["chat", "settings", "new"]

    # Re-install is idempotent (app relaunch in tests).
    assert menu_module.install_menu_bar(QApplication.instance(), shell) is bar


def test_reduce_motion_skips_decorative_fade(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(a11y, "reduce_motion_enabled", lambda: True)
    target = widgets_module.UserBubble("hello")
    widgets_module.fade_in(target)
    assert target.graphicsEffect() is None  # no opacity animation applied
    assert not hasattr(target, "_fade_anim")

    monkeypatch.setattr(a11y, "reduce_motion_enabled", lambda: False)
    target2 = widgets_module.UserBubble("hello")
    widgets_module.fade_in(target2)
    assert target2.graphicsEffect() is not None
    assert getattr(target2, "_fade_anim", None) is not None


def test_pet_reduce_motion_uses_static_status_and_pose(qtbot, monkeypatch) -> None:
    from haochen_app.pet import bubble as bubble_module
    from haochen_app.pet import pet_window as pet_window_module

    monkeypatch.setattr(a11y, "reduce_motion_enabled", lambda: True)
    status = bubble_module.StatusBlock("正在组织回答")
    qtbot.addWidget(status)
    assert not status._pulse.isActive()

    pet = pet_window_module.PetWindow()
    qtbot.addWidget(pet)
    assert not pet._float_timer.isActive()
    pet.show()
    pet.set_pose("thinking")
    assert pet.label.graphicsEffect() is None


def test_screen_of_falls_back_to_primary(qtbot) -> None:
    assert a11y.screen_of(None) is QApplication.primaryScreen()
    hidden = ChatWindow(FakeClient())
    qtbot.addWidget(hidden)
    assert a11y.screen_of(hidden) is not None


def test_chat_window_geometry_is_remembered_and_restored(qtbot) -> None:
    client = FakeClient()
    window = ChatWindow(client)
    qtbot.addWidget(window)
    window.show()

    window.move(140, 160)
    window.resize(1024, 720)
    window.close()
    qtbot.wait(50)

    geometry_file = client.home / "chat-window-geometry.json"
    assert geometry_file.exists()
    saved = json.loads(geometry_file.read_text(encoding="utf-8"))
    assert saved["width"] == 1024 and saved["height"] == 720
    assert abs(saved["x"] - 140) <= 4 and abs(saved["y"] - 160) <= 4
    assert geometry_file.stat().st_mode & 0o077 == 0

    restored = ChatWindow(client)
    qtbot.addWidget(restored)
    assert restored.width() == 1024
    assert restored.height() == 720


def test_key_controls_have_accessible_names(qtbot) -> None:
    window = ChatWindow(FakeClient())
    qtbot.addWidget(window)

    assert window.btn_send.accessibleName() == "发送"
    assert window.btn_stop.accessibleName() == "停止生成"
    assert window.jump_to_latest_button.accessibleName() == "回到最新"
    assert window.sidebar.new_button.accessibleName() == "新会话"

    window._on_engine_event(
        {
            "type": "tool_execution_start",
            "toolCallId": "tc-a11y",
            "toolName": "write",
            "args": {"file_path": "/tmp/a.md", "content": "x"},
        }
    )
    card = window._tool_cards["tc-a11y"]
    assert card.toggle.accessibleName() == "工具详情"
    assert card.cancel_button.accessibleName() == "取消工具"
    assert card.retry_button.accessibleName() == "重试"
    assert card.open_button.accessibleName() == "打开产物"
    window.ctrl._phase = ""


def test_bubble_placement_uses_pet_screen(qtbot, monkeypatch) -> None:
    pet_module = importlib.import_module("haochen_app.pet.app")
    client = FakeClient()
    pet = pet_module.PetApp(client=client, supervisor=None)
    qtbot.addWidget(pet.pet)
    qtbot.addWidget(pet.bubble)

    class FakeScreen:
        def __init__(self, left, top):
            self._geo = left, top

        def availableGeometry(self):
            left, top = self._geo
            return SimpleNamespace(left=left, top=top, right=left + 1000,
                                    bottom=top + 800)

    secondary = SimpleNamespace(availableGeometry=lambda: QRect(-1440, 0, 1000, 800))
    monkeypatch.setattr(
        a11y, "screen_of", lambda _w: secondary
    )
    pet.pet.move(-1000, 400)
    pet._place_bubble()
    assert pet.bubble.x() >= -1440 and pet.bubble.x() <= -440 - pet.bubble.width()


def test_geometry_saved_with_tolerance(qtbot) -> None:
    # 独立小用例：offscreen 平台对 move 有 ±4px 偏移，用容差断言。
    client = FakeClient()
    window = ChatWindow(client)
    qtbot.addWidget(window)
    window.show()
    window.move(140, 160)
    window.resize(1024, 720)
    window.close()
    qtbot.wait(50)
    saved = json.loads((client.home / "chat-window-geometry.json").read_text(encoding="utf-8"))
    assert abs(saved["x"] - 140) <= 4
    assert abs(saved["y"] - 160) <= 4
