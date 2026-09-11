"""ChatWindow flows: history rendering, detail mode, sidebar, auto-title."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QTextCursor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import test_session_deletion_ui as harness  # noqa: E402

pet_state = importlib.import_module("haochen_app.pet.state")
window_module = harness.window_module
widgets_module = importlib.import_module("haochen_app.chat.widgets")
UserBubble = widgets_module.UserBubble
AssistantBubble = widgets_module.AssistantBubble
ToolCard = widgets_module.ToolCard
BubbleRow = widgets_module.BubbleRow
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


def test_real_chat_reloads_persisted_sessions_for_sidebar(qtbot, tmp_path: Path) -> None:
    sessions = tmp_path / "pi-sessions"
    sessions.mkdir()
    path = sessions / "saved.jsonl"
    path.write_text(
        json.dumps({"type": "session", "id": "saved", "timestamp": "2026-09-09"})
        + "\n"
        + json.dumps({"type": "message", "message": {"role": "user", "content": [
            {"type": "text", "text": "重新打开后还在吗"},
        ]}})
        + "\n"
        + json.dumps({"type": "session_info", "name": "重启后的会话"})
        + "\n",
        encoding="utf-8",
    )
    client = FakeClient()
    client.home = tmp_path
    client._mock = False

    window = ChatWindow(client)
    qtbot.addWidget(window)

    assert window._sessions == [{"path": str(path), "title": "重启后的会话"}]
    assert window.sidebar.list.item(0).text() == "重启后的会话"


def test_startup_ignores_prospective_empty_session_while_restoring_history(
    qtbot, tmp_path: Path
) -> None:
    sessions = tmp_path / "pi-sessions"
    sessions.mkdir()
    saved = sessions / "saved.jsonl"
    saved.write_text(
        json.dumps({"type": "session", "id": "saved", "timestamp": "2026-09-09"})
        + "\n",
        encoding="utf-8",
    )
    client = FakeClient()
    client.home = tmp_path
    client._mock = False
    window = ChatWindow(client)
    qtbot.addWidget(window)
    window.coordinator.set_current_session(str(saved))
    fresh = sessions / "not-created-yet.jsonl"

    window._on_state({"success": True, "data": {
        "sessionFile": str(fresh),
        "sessionName": "新会话",
        "model": {"id": "deepseek-v4-flash-vision-exp"},
    }})

    assert all(session["path"] != str(fresh) for session in window._sessions)
    assert window.coordinator.current_session == str(saved)


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


def test_user_bubble_preserves_literal_multiline_text(qtbot) -> None:
    bubble = UserBubble("第一行\n第二行\n第三行")
    qtbot.addWidget(bubble)

    assert bubble.view.toPlainText() == "第一行\n第二行\n第三行"


def test_plain_assistant_history_is_rendered_as_visible_conclusion(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)

    window._render_history({
        "success": True,
        "data": {"messages": [
            {"role": "assistant", "content": [{"type": "text", "text": "好"}]},
        ]},
    })

    answers = window.findChildren(AssistantBubble)
    assert len(answers) == 1
    assert answers[0].kind == "summary"
    assert answers[0].view.toPlainText() == "好"


def test_aborted_history_is_clearly_marked_as_incomplete(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)

    window._render_history({
        "success": True,
        "data": {"messages": [
            {"role": "user", "content": [{"type": "text", "text": "写长文"}]},
            {"role": "assistant", "stopReason": "aborted", "content": [
                {"type": "text", "text": "【brief】概览【/brief】【detail】正文停在时空【/detail】"},
            ]},
        ]},
    })

    partials = [b for b in window.findChildren(AssistantBubble) if b.kind == "partial"]
    assert len(partials) == 1
    assert partials[0].tag is not None
    assert partials[0].tag.text() == "未完成内容"
    assert partials[0].view.toPlainText() == "正文停在时空"
    statuses = [s.label.text() for s in window.findChildren(window_module.StatusBubble)]
    assert any("以下是停止前" in text for text in statuses)
    assert any("上述内容未完成" in text for text in statuses)


def test_aborted_history_without_output_still_shows_stopped_status(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)

    window._render_history({"success": True, "data": {"messages": [
        {"role": "assistant", "stopReason": "aborted", "content": []},
    ]}})

    assert any("已停止生成" in s.label.text() for s in window.findChildren(window_module.StatusBubble))
    assert not window.findChildren(AssistantBubble)


def test_live_aborted_turn_never_renders_a_normal_conclusion(qtbot, tmp_path: Path) -> None:
    window, client = make_window(qtbot, tmp_path)
    window._clear_flow()

    window.input.setPlainText("写长文")
    window._on_send()
    request_id = window.ctrl._answer_request_id
    client.response.emit({"id": request_id, "success": True})
    getattr(client, "event").emit({"type": "message_end", "message": {"role": "user"}})
    window._on_stop()
    getattr(client, "event").emit({"type": "agent_end", "messages": [
        {"role": "assistant", "stopReason": "aborted", "content": [
            {"type": "text", "text": "【brief】完整概览【/brief】【detail】只写了一半【/detail】"},
        ]},
    ]})

    answers = window.findChildren(AssistantBubble)
    assert any(b.kind == "partial" and "只写了一半" in b.view.toPlainText() for b in answers)
    assert not any(b.kind == "summary" for b in answers)


def test_private_resume_context_is_hidden_from_user_history(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    payload = (
        "<haochen_resume_context>\n半截上下文\n</haochen_resume_context>\n"
        "<haochen_user_message>\n继续说"
    )

    window._render_history({"success": True, "data": {"messages": [
        {"role": "user", "content": [{"type": "text", "text": payload}]},
    ]}})

    users = window.findChildren(UserBubble)
    assert len(users) == 1
    assert users[0].view.toPlainText() == "继续说"


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


def test_state_refresh_does_not_replace_user_derived_title(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window._sessions = [{"path": "/s/a.jsonl", "title": "请帮我解释为什么天空…"}]
    window._current_path = "/s/a.jsonl"

    window._on_state({
        "success": True,
        "data": {
            "sessionFile": "/s/a.jsonl",
            "sessionName": "请帮我解释为什么天空在晴朗白:",
            "model": {"id": "deepseek-v4-flash-vision-exp"},
        },
    })

    assert window._sessions[0]["title"] == "请帮我解释为什么天空…"


def test_full_chat_command_return_inserts_newline_instead_of_sending(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window.input.setPlainText("第一行")
    window.input.moveCursor(QTextCursor.MoveOperation.End)
    event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier,
    )

    window.input.keyPressEvent(event)

    assert window.input.toPlainText() == "第一行\n"
    assert not window.findChildren(UserBubble)


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
    assert window.detail_close_button.isHidden()
    assert window.detail_title.text() == "haochen"
    assert window.width() >= 820
    assert window.height() >= 560

    window.show_normal()
    assert window.isVisible()
    assert window.width() >= 820
    assert window.height() >= 560


def test_detail_mode_opens_at_latest_turn_after_history_layout(
    qtbot, tmp_path: Path,
) -> None:
    from PyQt6.QtCore import QRect

    window, _client = make_window(qtbot, tmp_path)
    messages: list[dict] = []
    for index in range(12):
        messages.extend([
            {"role": "user", "content": [{"type": "text", "text": f"问题 {index}"}]},
            {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": (
                        f"【brief】结论 {index}【/brief】\n"
                        f"【detail】这是第 {index} 轮的完整依据。" + "详细说明。" * 20
                        + "【/detail】"
                    ),
                }],
            },
        ])

    window.open_from_bubble(QRect(100, 100, 320, 240))
    window._render_history({"success": True, "data": {"messages": messages}})
    qtbot.wait(350)

    bar = window.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    assert bar.value() > bar.minimum()
    assert window._follow_stream is False


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


def test_detail_header_uses_current_topic_and_send_action_is_legible(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window._sessions = [{"path": "/s/topic.jsonl", "title": "东京夜游建议"}]
    window._current_path = "/s/topic.jsonl"

    window.open_from_bubble()

    assert window.detail_title.text() == "东京夜游建议"
    assert "发送" in window.btn_send.text()
    assert window.btn_send.width() >= 80


def test_short_history_starts_at_top_without_a_blank_scan_area(qtbot, tmp_path: Path) -> None:
    window, _client = make_window(qtbot, tmp_path)
    window._render_history({"success": True, "data": {"messages": [
        {"role": "assistant", "content": [{"type": "text", "text": "简短回答"}]},
    ]}})

    assert isinstance(window.flow.itemAt(0).widget(), BubbleRow)
    assert window.flow.itemAt(window.flow.count() - 1).spacerItem() is not None


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
