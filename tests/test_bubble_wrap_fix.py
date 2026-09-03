"""Bubble long-CJK-text wrap regression + humanized error display (user-reported 0.2.0 bug)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

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
