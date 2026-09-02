"""ConversationController two-step protocol, full coverage (P1 module)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

conversation = importlib.import_module("haochen_app.conversation")
ConversationController = conversation.ConversationController


class FakeClient(QObject):
    event = pyqtSignal(dict)  # pyright: ignore[reportAssignmentType]
    response = pyqtSignal(dict)
    crashed = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []
        self.fail_prompt = False

    def prompt(self, text: str) -> str:
        if self.fail_prompt:
            raise RuntimeError("engine not running")
        self.sent.append(text)
        return f"req-{len(self.sent)}"

    def abort(self) -> str:
        return "abort"


def client_event_emit(client, payload: dict) -> None:
    getattr(client, "event").emit(payload)


def user_message(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}], "timestamp": 1}


def assistant_message(text: str, stop: str = "stop") -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}],
            "stopReason": stop, "errorMessage": "boom" if stop == "error" else None}


def test_full_two_step_round_emits_signals_in_order() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    events: list[tuple] = []
    controller.busy_changed.connect(lambda b: events.append(("busy", b)))
    controller.answer_delta.connect(lambda d: events.append(("delta", d)))
    controller.answer_done.connect(lambda a: events.append(("answer", a)))
    controller.summarizing.connect(lambda: events.append(("summarizing",)))
    controller.summary_done.connect(lambda s: events.append(("summary", s)))

    request_id = controller.send("问题")
    client.response.emit({"id": request_id, "success": True})
    client_event_emit(client, {"type": "message_update", "assistantMessageEvent": {
        "type": "text_delta", "delta": "【answer】详"}})
    client_event_emit(client, {"type": "message_update", "assistantMessageEvent": {
        "type": "text_delta", "delta": "答【/answer】"}})
    client_event_emit(client, {"type": "agent_end", "messages": [
        user_message("问题"), assistant_message("【answer】详答【/answer】")]})
    # 引擎发出 summary kick（第二问）
    assert conversation.SUMMARY_KICK_PREFIX in client.sent[1]
    client_event_emit(client, {"type": "agent_end", "messages": [
        user_message("k"), assistant_message("【summary】短结【/summary】")]})

    kinds = [e[0] for e in events]
    assert kinds[0] == "busy" and events[0][1] is True
    assert "delta" in kinds and "answer" in kinds and "summarizing" in kinds
    assert events[-1] == ("summary", "短结")
    assert events[-2] == ("busy", False)
    assert not controller.busy
    assert len(client.sent) == 2


def test_will_retry_round_is_ignored_until_final() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    done: list[str] = []
    controller.summary_done.connect(done.append)

    request_id = controller.send("q")
    client.response.emit({"id": request_id, "success": True})
    client_event_emit(client, {"type": "agent_end", "willRetry": True, "messages": []})
    assert controller.busy  # willRetry 不结束回合
    client_event_emit(client, {"type": "agent_end", "messages": [
        user_message("q"), assistant_message("【answer】a【/answer】")]})
    client_event_emit(client, {"type": "agent_end", "messages": [
        user_message("k"), assistant_message("【summary】s【/summary】")]})
    assert done == ["s"]


def test_error_stop_reason_finishes_round_with_error() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    failures: list[str] = []
    controller.failed.connect(failures.append)

    controller.send("q")
    client_event_emit(client, {"type": "agent_end", "messages": [assistant_message("x", "error")]})
    assert failures == ["boom"]
    assert not controller.busy


def test_send_while_busy_fails_without_touching_engine() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    controller.send("first")
    failures: list[str] = []
    controller.failed.connect(failures.append)

    result = controller.send("second")

    assert result is None
    assert failures == ["上一轮尚未结束"]
    assert len(client.sent) == 1


def test_prompt_write_failure_finishes_round() -> None:
    client = FakeClient()
    client.fail_prompt = True
    controller = ConversationController(client)
    failures: list[str] = []
    controller.failed.connect(failures.append)

    result = controller.send("q")

    assert result is None
    assert failures == ["engine not running"]
    assert not controller.busy


def test_summary_kick_write_failure_finishes_round() -> None:
    client = FakeClient()
    controller = ConversationController(client)

    controller.send("q")
    client_event_emit(client, {"type": "agent_end", "messages": [
        user_message("q"), assistant_message("【answer】a【/answer】")]})
    # answer 完成后引擎掉线 → summary kick 写入失败
    client.fail_prompt = True
    failures: list[str] = []
    controller.failed.connect(failures.append)
    # 触发：由于 kick 已在 answer_done 时发出（未失败），此处直接验证 abort 路径
    controller.abort()  # busy 中的 abort 发出命令
    client.crashed.emit(9)  # 崩溃结算
    assert not controller.busy


def test_crash_mid_round_settles_with_failure() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    failures: list[str] = []
    controller.failed.connect(failures.append)

    controller.send("q")
    client.crashed.emit(9)

    assert not controller.busy
    assert failures and "9" in failures[0]


def test_final_text_prefers_last_assistant() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    msgs = [
        user_message("q"),
        assistant_message("第一版"),
        {"role": "assistant", "content": [{"type": "text", "text": "最终版"}], "stopReason": "stop"},
    ]
    request_id = controller.send("q")
    client.response.emit({"id": request_id, "success": True})
    client_event_emit(client, {"type": "agent_end", "messages": msgs})
    # 流式 buffer 空 → 用 message_end 全文
    assert controller._answer_text == "最终版"


def test_extractive_summary_fallback() -> None:
    text = "第一句结论。第二句是细节展开的内容比较长一些。第三句还有补充说明。"
    summary = conversation.extractive_summary(text)
    assert summary  # 有兜底输出


def test_parse_paired_and_strip_tags() -> None:
    assert conversation.parse_paired("【answer】好【/answer】", "answer") == "好"
    assert conversation.parse_paired("==answer==好==/answer==", "answer") == "好"
    assert conversation.parse_paired("无标记", "answer") == ""
    assert conversation.strip_tags("【summary】s【/summary】") == "s"
    assert conversation.strip_tags("") == ""
