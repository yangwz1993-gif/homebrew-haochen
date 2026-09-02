"""Streaming render throttle and user-aware scrolling (interaction-spec §3)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from PyQt6.QtWidgets import QScrollBar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

window_module = importlib.import_module("haochen_app.chat.window")
ChatWindow = window_module.ChatWindow

# Reuse the FakeClient harness from the deletion UI tests.
import test_session_deletion_ui as harness  # noqa: E402

FakeClient = harness.FakeClient


def make_window(qtbot, *, mock: bool = True):
    window = ChatWindow(FakeClient(mock=mock))
    qtbot.addWidget(window)
    window.resize(1000, 600)
    window.show()
    return window


def set_at_bottom(window, at_bottom: bool) -> None:
    bar: QScrollBar = window.scroll.verticalScrollBar()
    bar.setValue(bar.maximum() if at_bottom else 0)
    window._on_scroll_moved()


def test_streaming_render_is_throttled_not_per_delta(qtbot) -> None:
    window = make_window(qtbot)
    render_count = {"n": 0}
    original = window_module.AssistantBubble.append_stream

    def counting(self, buf):
        render_count["n"] += 1
        original(self, buf)

    window_module.AssistantBubble.append_stream = counting
    try:
        for i in range(20):
            window._on_answer_delta("字" * 10)
            qtbot.wait(2)
        # 节流窗口到期后必须渲染一次
        qtbot.waitUntil(lambda: render_count["n"] >= 1, timeout=int(window.STREAM_THROTTLE_MS * 4))
    finally:
        window_module.AssistantBubble.append_stream = original

    # 20 deltas within the throttle window must collapse to few renders.
    assert render_count["n"] <= 6, f"rendered {render_count['n']} times for 20 deltas"
    assert window._stream_row is not None
    window._flush_stream()
    assert len(window._stream_row.content._text) == 200
    # Flushed on completion regardless of timer.
    window._on_answer_done("完整回答")
    assert window._stream_row is None


def test_scroll_follows_only_when_user_is_at_bottom(qtbot) -> None:
    window = make_window(qtbot)
    for i in range(30):
        window._on_answer_delta("这是一段比较长的内容，用来撑出滚动区域。" * 4)
        qtbot.wait(2)
    qtbot.waitUntil(lambda: window._stream_row is not None, timeout=1000)
    window._flush_stream()
    qtbot.wait(100)

    bar = window.scroll.verticalScrollBar()
    assert bar.maximum() > 0

    # User scrolled up: streaming must not yank them down.
    set_at_bottom(window, False)
    before = bar.value()
    window._on_answer_delta("继续追加的内容，不应该把视图拽走" * 10)
    qtbot.wait(80)
    window._flush_stream()
    qtbot.wait(50)
    assert bar.value() == before
    assert window.jump_to_latest_button.isVisibleTo(window)

    # Jump-to-latest returns to the bottom and hides itself.
    window.jump_to_latest_button.click()
    qtbot.wait(30)
    assert bar.value() == bar.maximum()
    assert not window.jump_to_latest_button.isVisibleTo(window)


def test_at_bottom_streaming_keeps_following(qtbot) -> None:
    window = make_window(qtbot)
    for i in range(20):
        window._on_answer_delta("底部跟随模式的内容，同样需要足够长。" * 4)
        qtbot.wait(2)
    qtbot.waitUntil(lambda: window._stream_row is not None, timeout=1000)
    set_at_bottom(window, True)
    window._on_answer_delta("继续追加，底部应继续跟随。" * 8)
    qtbot.wait(80)
    window._flush_stream()
    qtbot.wait(50)
    bar = window.scroll.verticalScrollBar()
    assert bar.value() == bar.maximum()
    assert not window.jump_to_latest_button.isVisibleTo(window)


def test_throttle_flushes_within_bounded_latency(qtbot) -> None:
    window = make_window(qtbot)
    window._on_answer_delta("首个增量")
    qtbot.waitUntil(
        lambda: window._stream_row is not None and len(window._stream_row.content._text) >= 4,
        timeout=int(window.STREAM_THROTTLE_MS * 3),
    )
