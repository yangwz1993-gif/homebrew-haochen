"""Acceptance regressions: consistent chrome and a stationary desktop character."""

import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from haochen_app.app_shell import AppShell
from haochen_app.chat.placement import detail_rect
from haochen_app.chat.theme import C as CHAT_COLORS
from haochen_app.pet.theme import COLORS as PET_COLORS
from test_chat_flows import make_window


@pytest.mark.parametrize("screen", [QRect(0, 25, 1470, 890), QRect(-1920, -180, 1920, 1080),
                                         QRect(0, 25, 800, 575)])
@pytest.mark.parametrize("x_fraction,y_fraction", [(0, 0), (1, 0), (0, 1), (1, 1), (.5, .5)])
def test_detail_fits_free_screen_space(screen, x_fraction, y_fraction):
    pet = QRect(screen.x() + int((screen.width() - 128) * x_fraction),
                screen.y() + int((screen.height() - 150) * y_fraction), 128, 150)
    target = detail_rect(screen, pet, QRect())
    assert screen.contains(target)
    assert not target.intersects(pet)
    assert target.width() >= 250 and target.height() >= 300


def test_both_entry_points_share_one_palette():
    assert CHAT_COLORS is PET_COLORS
    assert CHAT_COLORS["bg"] == CHAT_COLORS["surface"] == "#fafafa"


def test_integrated_header_drags_window_without_affecting_content(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path)
    window.move(100, 100)
    header = window.detail_header
    no_modifier = Qt.KeyboardModifier.NoModifier
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(200, 16), QPointF(300, 116),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, no_modifier)
    header.mousePressEvent(press)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(230, 56), QPointF(330, 156),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, no_modifier)
    header.mouseMoveEvent(move)
    assert window.pos().x() == 130 and window.pos().y() == 140
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(230, 56), QPointF(330, 156),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, no_modifier)
    header.mouseReleaseEvent(release)
    assert header._drag_offset is None


def test_detail_transition_does_not_move_pet_or_resize_text(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("haochen_app.a11y.reduce_motion_enabled", lambda: False)
    shell = AppShell(mock=True, home=tmp_path)
    shell.pet.pet._float_timer.stop()  # Exclude the intentional two-pixel idle breathing.
    for widget in (shell.chat, shell.settings, shell.pet.bubble, shell.pet.pet):
        qtbot.addWidget(widget)
    area = QApplication.primaryScreen().availableGeometry()
    shell.pet.pet.move(area.right() - shell.pet.pet.width() - 16,
                       area.bottom() - shell.pet.pet.height() - 16)
    shell.pet.pet.show()
    origin = shell.pet.pet.pos()
    shell.pet.bubble.input.setPlainText("未发送的草稿")
    shell._open_pet_detail(QRect(area.right() - 380, area.bottom() - 300, 350, 100))
    size = shell.chat.size()
    for _ in range(12):
        qtbot.wait(20)
        assert shell.pet.pet.pos() == origin
        assert shell.chat.size() == size
        assert not shell.chat.geometry().intersects(shell.pet.pet.geometry())
    assert shell.chat.input.toPlainText() == "未发送的草稿"
    shell.chat.input.setPlainText("在详情里改过的草稿")
    assert shell.pet.bubble.input.toPlainText() == "在详情里改过的草稿"
    qtbot.keyClick(shell.chat, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not shell.chat.isVisible())
    assert shell.pet.pet.pos() == origin
    qtbot.wait(350)  # Let owned layout callbacks settle before Qt fixture teardown.
    assert shell.pet.bubble.input.toPlainText() == "在详情里改过的草稿"
    shell._open_pet_detail(QRect())
    qtbot.wait(220)
    shell.chat.detail_close_button.click()
    qtbot.waitUntil(lambda: not shell.chat.isVisible())
    assert shell.pet.pet.pos() == origin


def test_reopening_same_turn_preserves_reading_position(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path)
    messages = []
    for index in range(15):
        messages.extend([
            {"role": "user", "content": [{"type": "text", "text": f"问题 {index}"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "细节。" * 100}]},
        ])
    history = {"success": True, "data": {"messages": messages}}
    window.open_from_bubble(QRect(300, 200, 300, 90), "问题 14")
    window._render_history(history)
    qtbot.wait(450)
    bar = window.scroll.verticalScrollBar()
    assert bar.maximum() > 1000
    bar.setValue(380)
    window.collapse_detail()
    qtbot.waitUntil(lambda: not window.isVisible())
    window.open_from_bubble(QRect(300, 200, 300, 90), "问题 14")
    window._render_history(history)
    qtbot.wait(450)
    assert abs(bar.value() - 380) <= 2


def test_intro_title_is_weak_and_long_title_cannot_stretch_window(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path)
    window._current_path = "/test/session"
    window._sessions = [{"path": window._current_path, "title": "你好，请用一句话介绍自己"}]
    window.open_from_bubble()
    qtbot.wait(250)
    assert window.detail_title.text() == "会话详情"
    initial = window.width()
    window._sessions[0]["title"] = "一个很长的用户会话标题" * 40
    window._update_detail_title()
    qtbot.wait(20)
    assert window.width() == initial
    assert window.detail_title.text().endswith("…")
    assert window.detail_close_button.text() == ""
    assert window.detail_close_button.accessibleName() == "收起会话详情"
