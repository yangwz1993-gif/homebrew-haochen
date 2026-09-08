"""ChatWindow flows: history rendering, detail mode, sidebar, auto-title."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import test_session_deletion_ui as harness  # noqa: E402

pet_state = importlib.import_module("haochen_app.pet.state")
window_module = harness.window_module
widgets_module = importlib.import_module("haochen_app.chat.widgets")
UserBubble = widgets_module.UserBubble
AssistantBubble = widgets_module.AssistantBubble
ToolCard = widgets_module.ToolCard
ErrorBanner = window_module.ErrorBanner
widgets_module = importlib.import_module("haochen_app.chat.widgets")
ChatWindow = harness.ChatWindow
FakeClient = harness.FakeClient


def make_window(qtbot, tmp_path: Path):
    client = FakeClient()
    client.home = tmp_path
    window = ChatWindow(client)
    qtbot.addWidget(window)
    window.show()
    return window, client


def test_render_history_draws_users_answers_tools_and_errors(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window._clear_flow()
    window._render_history({
        "success": True,
        "data": {"messages": [
            {"role": "user", "content": [{"type": "text", "text": "用户问题"}]},
            {"role": "user", "content": [{"type": "text", "text": "haochen-summary-phase\nkick"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "【answer】详答【/answer】"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "【summary】结论【/summary】"}]},
            {"role": "assistant", "content": [{"type": "text", "text":
             "【brief】新结论【/brief】\n【detail】新详情【/detail】"}]},
            {"role": "assistant", "stopReason": "error", "errorMessage": "坏轮"},
            {"role": "toolResult", "toolCallId": "t1", "toolName": "bash",
             "content": [{"type": "text", "text": "输出"}], "isError": False},
        ]},
    })
    from haochen_app.chat.widgets import AssistantBubble, ToolCard, UserBubble  # noqa: E402
    from haochen_app.chat.window import ErrorBanner  # noqa: E402
    assert window.findChildren(UserBubble)
    answers = [b for b in window.findChildren(AssistantBubble)]
    assert any("详答" in (b.view.toPlainText()) for b in answers)
    assert any("结论" in b.view.toPlainText() for b in answers)
    assert any("新结论" in b.view.toPlainText() for b in answers)
    assert any("新详情" in b.view.toPlainText() for b in answers)
    assert window.findChildren(ToolCard)
    assert window.findChildren(ErrorBanner)


def test_render_history_failure_is_silent(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window._clear_flow()
    window._render_history({"success": False})
    assert not window.findChildren(UserBubble)


def test_render_history_collapses_identical_error_retries(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    pair = [
        {"role": "user", "content": [{"type": "text", "text": "帮我看看"}]},
        {"role": "assistant", "stopReason": "error", "errorMessage": "missing API key"},
    ]

    window._render_history({"success": True, "data": {"messages": pair * 3}})

    assert len(window.findChildren(UserBubble)) == 1
    assert len(window.findChildren(ErrorBanner)) == 1


def test_auto_title_renames_current_new_session(qtbot, tmp_path: Path) -> None:
    window, client = make_window(qtbot, tmp_path)
    window._sessions = [{"path": "/s/a.jsonl", "title": "新会话"}]
    window._current_path = "/s/a.jsonl"

    window._auto_title("这是一个很长的首条消息内容超过二十个字")

    assert window._sessions[0]["title"].startswith("这是一个很长的首条消息")
    assert any(r.startswith("set_session_name-") for r in window._pending_rpc)


def test_auto_title_skips_named_sessions(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window._sessions = [{"path": "/s/a.jsonl", "title": "已有名字"}]
    window._current_path = "/s/a.jsonl"

    window._auto_title("新消息")

    assert window._sessions[0]["title"] == "已有名字"
    assert not any(r.startswith("set_session_name-") for r in window._pending_rpc)


def test_state_refresh_replaces_existing_new_session_title(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window._sessions = [{"path": "/s/a.jsonl", "title": "新会话"}]
    window._current_path = "/s/a.jsonl"

    window._on_state({
        "success": True,
        "data": {
            "sessionFile": "/s/a.jsonl",
            "sessionName": "2+2 等于几？",
            "model": {"id": "deepseek-v4-flash-vision-exp"},
        },
    })

    assert window._sessions == [{"path": "/s/a.jsonl", "title": "2+2 等于几？"}]
    assert window.sidebar.list.item(0).text() == "2+2 等于几？"


def test_rename_session_switches_engine_when_not_current(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window._sessions = [
        {"path": "/s/a.jsonl", "title": "A"},
        {"path": "/s/b.jsonl", "title": "B"},
    ]
    window._current_path = "/s/a.jsonl"

    window._rename_session("/s/b.jsonl", "新名字B")

    assert any(r.startswith("switch-") for r in window._pending_rpc)
    switch_id = next(r for r in window._pending_rpc if r.startswith("switch-"))
    window._on_response({"id": switch_id, "success": True, "data": {"cancelled": False}})
    messages_id = next(
        (r for r in window._pending_rpc if r.startswith("messages-")), None
    )
    if messages_id:
        window._on_response({"id": messages_id, "success": True, "data": {"messages": []}})
    name_id = next(
        (r for r in window._pending_rpc if r.startswith("set_session_name-")), None
    )
    assert name_id is not None
    window._on_response({"id": name_id, "success": True, "data": {}})
    assert window._sessions[1]["title"] == "新名字B"
    assert window._current_path == "/s/b.jsonl"


def test_detail_mode_expand_and_collapse(qtbot, tmp_path: Path) -> None:
    from PyQt6.QtCore import QRect

    window, _client = make_window(qtbot, tmp_path)
    collapsed: list[bool] = []
    window.detail_collapsed.connect(lambda: collapsed.append(True))

    window.open_from_bubble(QRect(100, 100, 320, 240))
    assert window._detail_mode is True
    assert window.isVisible()
    assert window.sidebar.isHidden()
    assert not window.detail_header.isHidden()
    assert window.width() <= 840
    assert window.height() <= 600
    assert "继续这个话题" in window.input.placeholderText()

    window.collapse_detail()
    qtbot.waitUntil(lambda: bool(collapsed), timeout=2000)
    assert window._detail_mode is False
    assert not window.isVisible()
    assert not window.sidebar.isHidden()
    assert window.detail_header.isHidden()
    assert window.width() >= 820
    assert window.height() >= 560

    window.show_normal()
    assert window.isVisible()
    assert window.width() >= 820
    assert window.height() >= 560


def test_unclosed_duplicate_short_answer_renders_once(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window._clear_flow()

    window._render_history_assistant({
        "role": "assistant",
        "content": [{"type": "text", "text": "【brief】4。【/brief】\n【detail】4。"}],
    })

    answers = window.findChildren(AssistantBubble)
    assert len(answers) == 1
    assert answers[0].view.toPlainText().strip() == "4。"


def test_detail_bubbles_have_distinct_section_labels(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    summary = AssistantBubble("summary")
    detail = AssistantBubble("answer")
    qtbot.addWidget(summary)
    qtbot.addWidget(detail)

    assert summary.tag is not None and summary.tag.text() == "结论"
    assert detail.tag is not None and detail.tag.text() == "依据与细节"


def test_sidebar_uses_readable_model_alias(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    model_id = "deepseek-v4-flash-vision-exp"

    window.sidebar.set_model(model_id)

    assert window.sidebar.status.text() == "模型  DeepSeek V4 Vision · 实验版"
    assert window.sidebar.status.toolTip() == model_id
    assert "»" not in window.sidebar.status.text()


def test_maximized_layout_keeps_messages_and_composer_on_reading_column(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window.resize(1500, 900)
    qtbot.wait(30)

    flow_margins = window.flow.contentsMargins()
    input_margins = window._input_bar.contentsMargins()

    assert flow_margins.left() >= 100
    assert flow_margins.left() == flow_margins.right()
    assert input_margins.left() == flow_margins.left() + 16
    assert input_margins.right() == flow_margins.right() + 16
    assert window._bubble_max_w() <= 680


def test_escape_in_detail_collapses_instead_of_stopping(qtbot, tmp_path: Path) -> None:
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QKeyEvent

    window, _client = make_window(qtbot, tmp_path)
    window.open_from_bubble(QRect(50, 50, 300, 200))
    assert window._detail_mode

    event = QKeyEvent(
        QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
    )
    window.keyPressEvent(event)

    qtbot.waitUntil(lambda: not window._detail_mode, timeout=2000)


def test_foreign_turn_end_mirrors_history_when_visible(qtbot, tmp_path: Path) -> None:
    window, client = make_window(qtbot, tmp_path)
    window.ctrl._phase = ""  # 非本入口回合
    requested: list[str] = []
    original = window.client.get_state
    window.client.get_state = lambda: requested.append("state") or original()
    window.client.get_messages = lambda: requested.append("messages") or "m-1"

    window._on_foreign_turn_end({"type": "agent_end"})

    qtbot.wait(50)
    assert "state" in requested and "messages" in requested
