"""Bubble long-CJK-text wrap regression + humanized error display (user-reported 0.2.0 bug)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

bubble_module = importlib.import_module("haochen_app.pet.bubble")
BubbleWindow = bubble_module.BubbleWindow
conversation = importlib.import_module("haochen_app.conversation")


def test_long_cjk_text_wraps_inside_bubble_without_clipping(qtbot) -> None:
    win = BubbleWindow()
    qtbot.addWidget(win)
    win.show()

    # 300 个无空格中文长句：0.2.0 曾被 QLabel 最小宽度撑破 viewport 导致横向裁字
    long_text = "这是一段没有任何空格的中文长句用来复现横向裁字问题。" * 10
    win.add_summary(long_text)
    qtbot.wait(120)  # 等布局/动画落地

    viewport = win.scroll.viewport().width()
    host = win.flow_host
    assert host.width() <= viewport + 1, (
        f"flow_host {host.width()}px 超出 viewport {viewport}px → 横向裁字"
    )
    # 最小高度爆炸回归：两块短内容的 flow_host 不得被撑到上千像素
    assert host.height() <= viewport + 200, (
        f"flow_host 高度 {host.height()}px 异常（viewport {viewport}px）→ minimumSizeHint 爆炸"
    )

    labels = [lb for lb in win.flow_host.findChildren(QLabel) if long_text[:10] in lb.text()]
    assert labels, "摘要文本标签未找到"
    label = labels[0]
    assert label.width() <= viewport + 1, f"标签 {label.width()}px 超出 viewport {viewport}px"

    # 折行生效：高度明显高于单行
    line_h = label.fontMetrics().lineSpacing()
    assert label.height() >= line_h * 3, (
        f"标签高度 {label.height()}px 接近单行 {line_h}px → 文本未折行/被裁"
    )


def test_error_block_long_text_does_not_overflow(qtbot) -> None:
    win = BubbleWindow()
    qtbot.addWidget(win)
    win.show()

    long_error = '402: {"message":"Insufficient Balance","type":"unknown_error"}' + "详情" * 60
    win.add_error(long_error)
    qtbot.wait(120)

    viewport = win.scroll.viewport().width()
    assert win.flow_host.width() <= viewport + 1


def test_humanize_error_translates_provider_payload() -> None:
    raw = '402: {"message":"Insufficient Balance","type":"unknown_error","param":null,"code":"invalid"}'
    out = conversation.humanize_error(raw)
    assert "余额不足" in out
    assert "402" in out
    assert "unknown_error" not in out  # 原始 JSON 字段不再直出


def test_humanize_error_passthrough_for_plain_messages() -> None:
    assert conversation.humanize_error("引擎已退出") == "引擎已退出"
    assert conversation.humanize_error("") == ""
    assert conversation.humanize_error("503: 不是json") == "503: 不是json"


def test_input_state_drops_stale_result_height(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(bubble_module.a11y, "reduce_motion_enabled", lambda: True)
    win = BubbleWindow()
    qtbot.addWidget(win)
    win.summon()
    win.present_summary("结论。\n- 这是一个用于撑高旧结果状态的长要点。" * 8)
    qtbot.wait(50)

    win.start_input()
    qtbot.wait(50)
    metrics = win.layout_metrics()

    assert metrics["input_visible"] is True
    assert metrics["scroll_visible"] is False
    assert metrics["flow_content_height"] == 0
    assert metrics["input_top_gap"] <= 24
    assert metrics["height"] <= 108
    assert metrics["width"] == bubble_module.INPUT_WIDTH
    assert metrics["mode"] == "input"
    assert metrics["input_top_inset"] >= 8
    assert metrics["input_bottom_inset"] >= 6
    assert metrics["input_bottom"] < metrics["body_bottom"]
    assert win.input.placeholderText() == "问我点什么…"
    assert win.btn_send.width() == 38
    assert win.btn_send.height() == 34
    assert not win.btn_send.icon().isNull()
    assert win.btn_send.accessibleName() == "发送"
    assert not win.btn_open_chat.icon().isNull()
    assert win.btn_open_chat.accessibleName() == "展开完整会话"
    assert win.btn_close.isVisible()
    assert win.btn_close.accessibleName() == "关闭短气泡"


def test_input_composer_stays_inside_shell_when_tail_flips_top(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(bubble_module.a11y, "reduce_motion_enabled", lambda: True)
    win = BubbleWindow()
    qtbot.addWidget(win)
    win.summon()
    win.set_tail_anchor(win.x() + win.width() // 2, "top")
    win._refresh_height()
    qtbot.wait(20)

    metrics = win.layout_metrics()
    assert metrics["body_top"] == bubble_module.T.TAIL_SIZE
    assert metrics["input_top_inset"] >= 8
    assert metrics["input_bottom_inset"] >= 6
    assert metrics["input_bottom"] < metrics["body_bottom"]


def test_input_composer_keeps_body_padding_with_existing_result(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(bubble_module.a11y, "reduce_motion_enabled", lambda: True)
    win = BubbleWindow()
    qtbot.addWidget(win)
    win.present_summary("结论。\n- 已修复输入区越过气泡边框的问题。")
    win.set_input_visible(True)
    win._refresh_height()
    win.show()
    qtbot.wait(20)

    metrics = win.layout_metrics()
    assert metrics["flow_content_height"] > 0
    assert metrics["input_bottom_inset"] >= 8
    assert metrics["input_bottom"] < metrics["body_bottom"]


def test_input_grows_through_three_lines_before_scrolling(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(bubble_module.a11y, "reduce_motion_enabled", lambda: True)
    win = BubbleWindow()
    qtbot.addWidget(win)
    win.summon()

    win.input.setPlainText("line1\nline2")
    qtbot.wait(20)
    two_line_height = win.input.height()
    metrics = win.layout_metrics()
    assert 58 <= two_line_height <= 64
    assert win.input.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert win.input.document().documentLayout().documentSize().height() <= (
        win.input.viewport().height() + 1
    )
    assert metrics["input_bottom_inset"] >= 8

    win.input.setPlainText("line1\nline2\nline3")
    qtbot.wait(20)
    assert 76 <= win.input.height() <= 82
    assert win.input.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff

    win.input.setPlainText("line1\nline2\nline3\nline4")
    qtbot.wait(20)
    assert win.input.height() == 82
    assert win.input.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert win.input.verticalScrollBar().maximum() > 0


def test_first_interaction_hint_uses_real_wrapped_height_without_blank_band(
    qtbot, monkeypatch
) -> None:
    monkeypatch.setattr(bubble_module.a11y, "reduce_motion_enabled", lambda: True)
    win = BubbleWindow()
    qtbot.addWidget(win)
    win.start_input()
    win.add_greeting("直接在下方问我；右键人物可打开完整对话和设置。")
    greet = win.findChildren(bubble_module.GreetBlock)[-1]
    win.set_input_visible(True)
    win.summon()
    qtbot.wait(20)

    metrics = win.layout_metrics()
    available_width = win.width() - win._root.contentsMargins().left() - win._root.contentsMargins().right()
    assert metrics["flow_content_height"] == greet.heightForWidth(available_width)
    assert metrics["flow_to_input_gap"] <= 8
    assert metrics["input_bottom_inset"] >= 8
    assert metrics["height"] <= 178


def test_work_and_result_use_distinct_compact_shells(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(bubble_module.a11y, "reduce_motion_enabled", lambda: True)
    win = BubbleWindow()
    qtbot.addWidget(win)
    win.summon()

    win.present_status("收到，我接住了", cancellable=True)
    progress = win.layout_metrics()
    assert progress["mode"] == "progress"
    assert progress["width"] == bubble_module.PROGRESS_WIDTH
    assert progress["height"] <= 92
    assert progress["input_visible"] is False

    win.present_summary("直接说结论：已经处理好了。")
    result = win.layout_metrics()
    assert result["mode"] == "result"
    assert result["width"] == bubble_module.BUBBLE_WIDTH
    assert result["input_visible"] is False


def test_humanize_error_redacts_invalid_authorization_value() -> None:
    raw = "Header 'Authorization' has invalid value 'Bearer sk-在此填入你的-DeepSeek-Key'"
    out = conversation.humanize_error(raw)

    assert out == "API Key 无效或格式不正确。请打开设置重新配置后再试。"
    assert "Bearer" not in out
    assert "sk-" not in out


def test_humanize_error_hides_cli_help_and_internal_paths() -> None:
    raw = (
        "No API key found for the selected model. Use /login to log into a provider. "
        "See /private/tmp/build/haochen.app/Contents/Resources/engine/docs/providers.md "
        "and models.md."
    )
    out = conversation.humanize_error(raw)

    assert out == "当前模型还没有配置 API Key。请打开设置，保存并验证后再试。"
    assert "/private" not in out
    assert "/login" not in out
    assert ".md" not in out
