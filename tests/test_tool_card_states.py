"""Tool card states: pending/running/success/failure/cancelled with cancel/retry/open (task-4b)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import test_session_deletion_ui as harness  # noqa: E402

FakeClient = harness.FakeClient
window_module = harness.window_module
widgets = importlib.import_module("haochen_app.chat.widgets")
ToolCard = widgets.ToolCard


def test_tool_card_lifecycle_states_and_colors(qtbot) -> None:
    card = ToolCard("call-1", "bash", {"command": "ls -la"})

    assert card.status.text() == "运行中…"
    card.mark_cancelled()
    assert card.status.text() == "已取消"

    card2 = ToolCard("call-2", "bash", {"command": "ls"})
    card2.mark_done("输出内容", is_error=True, elapsed_ms=1200)
    assert card2.status.text() == "✗ 失败"

    card3 = ToolCard("call-3", "bash", {"command": "ls"})
    card3.mark_done("输出内容", is_error=False, elapsed_ms=800)
    assert card3.status.text().startswith("✓ 完成")


def test_tool_card_shows_elapsed_time(qtbot) -> None:
    card = ToolCard("call-1", "bash", {"command": "ls"})
    card.mark_done("ok", is_error=False, elapsed_ms=1500)
    assert "1.5s" in card.status.text()


def test_tool_card_exposes_artifact_path(qtbot) -> None:
    card = ToolCard("call-1", "write", {"file_path": "/tmp/report.md", "content": "x"})
    assert card.artifact_path() == Path("/tmp/report.md")
    assert card.open_button.toolTip() == "/tmp/report.md"

    card2 = ToolCard("call-2", "bash", {"command": "ls"})
    assert card2.artifact_path() is None


class AbortCountingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.abort_calls = 0

    def abort(self) -> str:
        self.abort_calls += 1
        return super().abort()


def test_running_tool_can_be_cancelled_from_card(qtbot) -> None:
    client = AbortCountingClient()
    window = window_module.ChatWindow(client)
    qtbot.addWidget(window)

    window.ctrl._phase = "answer"  # simulate an active turn
    window._on_engine_event(
        {
            "type": "tool_execution_start",
            "toolCallId": "tc-1",
            "toolName": "bash",
            "args": {"command": "sleep 100"},
        }
    )
    card = window._tool_cards["tc-1"]
    assert card.status.text() == "运行中…"

    card.cancel_button.click()
    # 取消运行中的工具 = 中止当前回合（rpc abort 已发出）
    assert client.abort_calls == 1

    window._on_engine_event(
        {"type": "tool_execution_end", "toolCallId": "tc-1", "isError": False, "result": {"content": []}}
    )
    window.ctrl._phase = ""


def test_failed_tool_can_be_retried(qtbot) -> None:
    client = FakeClient()
    window = window_module.ChatWindow(client)
    qtbot.addWidget(window)

    window._last_user_text = "重试这条"
    window.ctrl._phase = "answer"
    window._on_engine_event(
        {
            "type": "tool_execution_start",
            "toolCallId": "tc-2",
            "toolName": "bash",
            "args": {"command": "false"},
        }
    )
    card = window._tool_cards["tc-2"]
    window._on_engine_event(
        {
            "type": "tool_execution_end",
            "toolCallId": "tc-2",
            "isError": True,
            "result": {"content": [{"type": "text", "text": "boom"}]},
        }
    )
    assert card.status.text() == "✗ 失败"

    # 失败后重试按钮可用，点击即把上一条消息重新入队。
    card.retry_button.click()
    assert window.coordinator.texts("chat") == ["重试这条"]
    window.ctrl._phase = ""
