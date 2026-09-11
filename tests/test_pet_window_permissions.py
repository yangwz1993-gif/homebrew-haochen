"""Permissions guide polling + pet window interactions (coverage completion)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QImage, QMouseEvent
from PyQt6.QtWidgets import QApplication, QMenu

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

permissions = importlib.import_module("haochen_app.permissions")
pet_window_module = importlib.import_module("haochen_app.pet.pet_window")
PetWindow = pet_window_module.PetWindow


def test_relaunch_app_dev_mode_skips(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    permissions.relaunch_app()  # 开发模式只记日志，不重启、不抛


def test_relaunch_app_frozen_schedules_and_quits(monkeypatch) -> None:
    popped: list[list[str]] = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/haochen.app/Contents/MacOS/haochen")
    monkeypatch.setattr(permissions.subprocess, "Popen", lambda cmd, **kw: popped.append(cmd))
    quit_calls: list[bool] = []
    from PyQt6.QtWidgets import QApplication as _QApp
    monkeypatch.setattr(_QApp, "quit", lambda: quit_calls.append(True))
    permissions.relaunch_app()
    assert popped and "open -n" in popped[0][2]
    assert quit_calls == [True]


def test_relaunch_spawn_failure_is_non_fatal(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/haochen.app/Contents/MacOS/haochen")

    def boom(cmd, **kw):
        raise OSError("nope")

    monkeypatch.setattr(permissions.subprocess, "Popen", boom)
    permissions.relaunch_app()  # 不抛


def test_ensure_permissions_all_granted_invokes_callback(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "accessibility_granted", lambda: True)
    monkeypatch.setattr(permissions, "screen_recording_granted", lambda: True)
    calls: list[bool] = []
    permissions.ensure_permissions(None, on_all_granted=lambda: calls.append(True))
    assert calls == [True]


def test_ensure_permissions_needs_screen_recording_starts_poll(monkeypatch, qtbot) -> None:
    monkeypatch.setattr(permissions, "accessibility_granted", lambda: True)
    monkeypatch.setattr(permissions, "screen_recording_granted", lambda: False)
    monkeypatch.setattr(permissions, "request_screen_recording", lambda: True)
    monkeypatch.setattr(permissions, "_POLL_INTERVAL_MS", 20)

    completed: list[bool] = []
    # 屏幕录制授权完成后 _on_granted 会弹“立即重启”模态框（无头会阻塞），
    # 这里替换为纯回调验证轮询→发现→回调的链路。
    monkeypatch.setattr(
        permissions,
        "_on_granted",
        lambda parent, cb, need_restart: cb(),
    )
    permissions.ensure_permissions(None, on_all_granted=lambda: completed.append(True))
    assert permissions._active_poll is not None
    monkeypatch.setattr(permissions, "screen_recording_granted", lambda: True)
    qtbot.waitUntil(lambda: bool(completed), timeout=2000)
    if permissions._active_poll is not None:
        permissions._active_poll.stop()
        permissions._active_poll = None


def test_open_settings_url_runs_open(monkeypatch) -> None:
    opened: list[list[str]] = []
    monkeypatch.setattr(permissions.subprocess, "Popen", lambda cmd, **kw: opened.append(cmd))
    permissions.open_settings()
    assert opened and opened[0][0] == "open"
    permissions.open_screen_settings()
    assert len(opened) == 2


# ── pet window ──────────────────────────────────────────────

def make_pet_window(qtbot, tmp_path: Path, monkeypatch=None) -> PetWindow:
    """构造 PetWindow 并把位置持久化重定向到 tmp_path（整个测试期间）。"""
    if monkeypatch is not None:
        monkeypatch.setattr(pet_window_module, "haochen_home", lambda: tmp_path)
        window = PetWindow(hotkey_hint="测试")
        qtbot.addWidget(window)
        return window
    original_home = pet_window_module.haochen_home
    pet_window_module.haochen_home = lambda: tmp_path
    try:
        window = PetWindow(hotkey_hint="测试")
    finally:
        pet_window_module.haochen_home = original_home
    qtbot.addWidget(window)
    return window


def mouse(window, kind, local: QPoint, button, buttons) -> QMouseEvent:
    return QMouseEvent(
        kind,
        QPointF(local),
        QPointF(window.mapToGlobal(local)),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def test_pet_uses_retina_physical_pixels_without_changing_logical_size(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    window = make_pet_window(qtbot, tmp_path, monkeypatch)
    monkeypatch.setattr(window, "devicePixelRatioF", lambda: 2.0)
    window._pixmaps.clear()

    pixmap = window._pixmap("idle")

    assert pixmap is not None
    assert pixmap.width() == pet_window_module.PET_SIZE * 2
    assert pixmap.devicePixelRatio() == 2.0
    assert round(pixmap.deviceIndependentSize().width()) == pet_window_module.PET_SIZE


def test_all_runtime_poses_use_complete_transparent_fullbody_assets() -> None:
    assert set(pet_window_module.POSE_ASSETS) == {"idle", "thinking", "angry"}
    for filename in pet_window_module.POSE_ASSETS.values():
        assert filename.endswith("-fullbody.png")
        image = QImage(str(pet_window_module.ASSETS_DIR / filename))
        assert not image.isNull()
        assert image.width() == image.height() == 1254
        assert image.hasAlphaChannel()
        # 透明边缘保证桌宠没有白底或生成器画进去的棋盘格。
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(image.width() - 1, image.height() - 1).alpha() == 0


def test_pose_transition_never_fades_skin_into_desktop_color(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(pet_window_module.a11y, "reduce_motion_enabled", lambda: False)
    window = make_pet_window(qtbot, tmp_path, monkeypatch)
    window.show()
    qtbot.wait(20)

    window.set_pose("thinking")
    effect = window.label.graphicsEffect()
    assert effect is not None
    assert effect.opacity() >= 0.94

    # 快速状态切换也不能遗留旧的低透明度 effect。
    window.set_pose("idle")
    effect = window.label.graphicsEffect()
    assert effect is not None
    assert effect.opacity() >= 0.94
    qtbot.waitUntil(lambda: window.label.graphicsEffect() is None, timeout=5000)
    assert window.label.graphicsEffect() is None
    assert window.pose == "idle"


def test_pet_window_drag_persists_position(qtbot, tmp_path: Path, monkeypatch) -> None:
    window = make_pet_window(qtbot, tmp_path, monkeypatch)
    center = QPoint(window.width() // 2, window.height() // 2)

    window.mousePressEvent(
        mouse(window, QMouseEvent.Type.MouseButtonPress, center,
              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    )
    assert window._drag_pos is not None
    window.mouseMoveEvent(
        mouse(window, QMouseEvent.Type.MouseMove, center + QPoint(60, 40),
              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    )
    window.mouseReleaseEvent(
        mouse(window, QMouseEvent.Type.MouseButtonRelease, center + QPoint(60, 40),
              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    )
    # offscreen 下 move 可能被平台钳制：位置保存路径本身已执行（_moved=True → save_position）。
    window.save_position()

    saved = tmp_path / "pet-pos.json"
    assert saved.exists()
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["x"] == window.x() and data["y"] == window.y()


def test_pet_drag_is_clamped_above_dock_visible_area(qtbot, tmp_path: Path, monkeypatch) -> None:
    window = make_pet_window(qtbot, tmp_path, monkeypatch)
    screen = QApplication.primaryScreen()
    area = screen.availableGeometry()

    clamped = window._clamped_position(
        QPoint(area.right() + 500, area.bottom() + 500),
        area.center(),
    )

    assert clamped.x() + window.width() - 1 <= area.right()
    assert clamped.y() + window.height() - 1 <= area.bottom()
    assert clamped.x() >= area.left() + pet_window_module._SCREEN_MARGIN
    assert clamped.y() >= area.top() + pet_window_module._SCREEN_MARGIN
    assert clamped.x() + window.width() - 1 <= area.right() - pet_window_module._SCREEN_MARGIN
    assert clamped.y() + window.height() - 1 <= area.bottom() - pet_window_module._SCREEN_MARGIN


def test_pet_window_position_restore_and_offscreen_fallback(qtbot, tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pet-pos.json").write_text(
        json.dumps({"x": -50000, "y": -50000}), encoding="utf-8"
    )
    window = make_pet_window(qtbot, tmp_path, monkeypatch)
    screen = QApplication.primaryScreen().availableGeometry()
    assert window.x() >= screen.left() - 10
    assert window.y() >= screen.top() - 10

    (tmp_path / "pet-pos.json").write_text("{broken", encoding="utf-8")
    make_pet_window(qtbot, tmp_path, monkeypatch)  # 损坏 → 默认位置，不抛


def test_pet_window_double_click_summons(qtbot, tmp_path: Path) -> None:
    window = make_pet_window(qtbot, tmp_path)
    summoned: list[bool] = []
    window.summon_requested.connect(lambda: summoned.append(True))
    center = QPoint(window.width() // 2, window.height() // 2)
    window._moved = False
    window.mouseDoubleClickEvent(
        mouse(window, QMouseEvent.Type.MouseButtonDblClick, center,
              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    )
    assert summoned == [True]


def test_pet_window_single_click_summons_without_double_click_duplicate(qtbot, tmp_path: Path) -> None:
    window = make_pet_window(qtbot, tmp_path)
    summoned: list[bool] = []
    window.summon_requested.connect(lambda: summoned.append(True))
    center = QPoint(window.width() // 2, window.height() // 2)

    window.mousePressEvent(mouse(
        window, QMouseEvent.Type.MouseButtonPress, center,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
    ))
    window.mouseReleaseEvent(mouse(
        window, QMouseEvent.Type.MouseButtonRelease, center,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
    ))
    qtbot.waitUntil(lambda: summoned == [True], timeout=800)

    summoned.clear()
    window.mousePressEvent(mouse(
        window, QMouseEvent.Type.MouseButtonPress, center,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
    ))
    window.mouseReleaseEvent(mouse(
        window, QMouseEvent.Type.MouseButtonRelease, center,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
    ))
    window.mouseDoubleClickEvent(mouse(
        window, QMouseEvent.Type.MouseButtonDblClick, center,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
    ))
    qtbot.wait(400)
    assert summoned == [True]


def test_pet_window_context_menu_emits_actions(qtbot, tmp_path: Path) -> None:
    window = make_pet_window(qtbot, tmp_path)
    new_sessions: list[bool] = []
    chats: list[bool] = []
    settings: list[bool] = []
    quit_requested: list[bool] = []
    window.new_session_requested.connect(lambda: new_sessions.append(True))
    window.open_chat_requested.connect(lambda: chats.append(True))
    window.settings_requested.connect(lambda: settings.append(True))
    window.quit_requested.connect(lambda: quit_requested.append(True))

    triggered: list = []

    def fake_popup(menu_self, *args, **kwargs):
        triggered.extend(menu_self.actions())

    original_popup = QMenu.popup
    QMenu.popup = fake_popup
    try:
        from PyQt6.QtGui import QContextMenuEvent

        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5))
        window.contextMenuEvent(event)
    finally:
        QMenu.popup = original_popup

    labels = [action.text() for action in triggered]
    assert "打开完整对话" in labels
    assert len(triggered) >= 6  # 唤起/气泡/完整对话/新会话/设置/退出


def test_pet_image_opens_context_menu_directly(qtbot, tmp_path: Path) -> None:
    window = make_pet_window(qtbot, tmp_path)
    shown_at: list[QPoint] = []
    assert window.label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    original_popup = QMenu.popup

    def fake_popup(menu_self, global_pos, *args, **kwargs):
        shown_at.append(global_pos)

    QMenu.popup = fake_popup
    try:
        local_pos = QPoint(12, 18)
        expected = window.label.mapToGlobal(local_pos)
        release = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            QPointF(local_pos),
            QPointF(expected),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        window.mouseReleaseEvent(release)
    finally:
        QMenu.popup = original_popup

    assert shown_at == [expected]


def test_pet_image_control_click_opens_context_menu(qtbot, tmp_path: Path) -> None:
    window = make_pet_window(qtbot, tmp_path)
    shown_at: list[QPoint] = []

    original_popup = QMenu.popup

    def fake_popup(menu_self, global_pos, *args, **kwargs):
        shown_at.append(global_pos)

    QMenu.popup = fake_popup
    try:
        local_pos = QPoint(20, 24)
        expected = window.label.mapToGlobal(local_pos)
        release = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            QPointF(local_pos),
            QPointF(expected),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.MetaModifier,
        )
        window.mouseReleaseEvent(release)
    finally:
        QMenu.popup = original_popup

    assert shown_at == [expected]
