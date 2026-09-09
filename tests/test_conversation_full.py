"""ConversationController single-turn brief/detail protocol and compatibility fallbacks."""

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


def test_full_single_turn_round_emits_structured_result() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    events: list[tuple] = []
    controller.busy_changed.connect(lambda b: events.append(("busy", b)))
    controller.answer_delta.connect(lambda d: events.append(("delta", d)))
    controller.answer_done.connect(lambda a: events.append(("answer", a)))
    controller.summarizing.connect(lambda: events.append(("summarizing",)))
    controller.summary_done.connect(lambda s: events.append(("summary", s)))
    turns = []
    controller.turn_done.connect(turns.append)

    request_id = controller.send("问题")
    client.response.emit({"id": request_id, "success": True})
    client_event_emit(client, {"type": "message_update", "assistantMessageEvent": {
        "type": "text_delta", "delta": "【brief】短结【/brief】"}})
    client_event_emit(client, {"type": "message_update", "assistantMessageEvent": {
        "type": "text_delta", "delta": "【detail】详答【/detail】"}})
    client_event_emit(client, {"type": "agent_end", "messages": [
        user_message("问题"),
        assistant_message("【brief】短结【/brief】\n【detail】详答【/detail】"),
    ]})

    kinds = [e[0] for e in events]
    assert kinds[0] == "busy" and events[0][1] is True
    assert "delta" in kinds and "answer" in kinds and "summarizing" in kinds
    assert events[-1] == ("summary", "短结")
    assert events[-2] == ("busy", False)
    assert not controller.busy
    assert len(client.sent) == 1
    assert turns == [conversation.TurnResult(
        brief="短结",
        detail="详答",
        raw="【brief】短结【/brief】\n【detail】详答【/detail】",
        fallback_used=False,
    )]


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
        user_message("q"), assistant_message("【brief】s【/brief】【detail】a【/detail】")]})
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


def test_aborted_turn_is_exposed_and_supplies_one_referential_follow_up() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    aborted: list[str] = []
    controller.turn_aborted.connect(aborted.append)

    controller.send("写一篇长文")
    client_event_emit(client, {"type": "agent_end", "messages": [
        user_message("写一篇长文"),
        assistant_message("【brief】概览【/brief】【detail】正文停在时空弯曲【/detail】", "aborted"),
    ]})
    assert aborted == ["正文停在时空弯曲"]

    controller.send("继续，用一句话告诉我刚才停在哪里")
    assert "正文停在时空弯曲" in client.sent[-1]
    assert "继续，用一句话告诉我刚才停在哪里" in client.sent[-1]
    assert conversation.visible_user_text(client.sent[-1]) == "继续，用一句话告诉我刚才停在哪里"


def test_aborted_context_is_not_injected_into_unrelated_next_question() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    controller.send("写长文")
    client_event_emit(client, {"type": "agent_end", "messages": [
        assistant_message("半截内容", "aborted"),
    ]})

    controller.send("今天天气如何")

    assert client.sent[-1] == "今天天气如何"


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


def test_legacy_answer_falls_back_without_second_prompt() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    summaries: list[str] = []
    answers: list[str] = []
    controller.summary_done.connect(summaries.append)
    controller.answer_done.connect(answers.append)
    controller.send("q")
    client_event_emit(client, {"type": "agent_end", "messages": [
        user_message("q"), assistant_message("【answer】a【/answer】")]})

    assert not controller.busy
    assert answers == ["a"]
    assert summaries == ["a"]
    assert len(client.sent) == 1


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
    assert conversation.parse_paired("【brief】短【/brief】", "brief") == "短"
    assert conversation.parse_paired("【detail】长【/detail】", "detail") == "长"
    assert conversation.parse_paired("【answer】好【/answer】", "answer") == "好"
    assert conversation.parse_paired("==answer==好==/answer==", "answer") == "好"
    assert conversation.parse_paired("无标记", "answer") == ""
    assert conversation.strip_tags("【summary】s【/summary】") == "s"
    assert conversation.strip_tags("") == ""


def test_parse_turn_result_falls_back_for_plain_text() -> None:
    result = conversation.parse_turn_result("第一句结论。\n- 第二点")

    assert result.detail == "第一句结论。\n- 第二点"
    assert result.brief
    assert result.fallback_used is True


def test_parse_turn_result_removes_duplicate_brief_heading() -> None:
    for brief in ("## 结论：可以直接使用。", "结论\n可以直接使用。"):
        result = conversation.parse_turn_result(
            f"【brief】{brief}【/brief】\n【detail】详细依据。【/detail】"
        )

        assert result.brief == "可以直接使用。"


def test_parse_turn_result_recovers_unclosed_detail_without_copying_brief() -> None:
    result = conversation.parse_turn_result(
        "【brief】4。【/brief】\n【detail】4。"
    )

    assert result.brief == "4。"
    assert result.detail == "4。"
    assert result.fallback_used is True


def test_parse_turn_result_removes_internal_runtime_sentence() -> None:
    result = conversation.parse_turn_result(
        "【brief】还缺候选项。【/brief】\n"
        "【detail】当前工作目录 pi-home 是空的。请给我候选项和取舍标准。【/detail】"
    )

    assert result.brief == "还缺候选项。"
    assert "pi-home" not in result.detail
    assert result.detail == "请给我候选项和取舍标准。"


def test_same_visible_text_ignores_protocol_tags_and_whitespace() -> None:
    assert conversation.same_visible_text("【detail】是。", "  是。\n")
    assert not conversation.same_visible_text("是。", "是，因为…")


def test_explicit_short_answer_constraints_are_enforced() -> None:
    raw = conversation.TurnResult(brief="等于 4。", detail="等于 4。", raw="raw")
    exact = conversation.apply_user_output_constraints("2+2 等于几？只给答案。", raw)
    yes_no = conversation.apply_user_output_constraints(
        "北京是中国首都吗？只回答是或否。",
        conversation.TurnResult(brief="是。", detail="是，因为北京是首都。"),
    )

    assert exact.brief == exact.detail == "4"
    assert yes_no.brief == yes_no.detail == "是"


def test_quoted_exact_reply_survives_unstructured_model_output() -> None:
    parsed = conversation.parse_turn_result("模型多说了一句")
    result = conversation.apply_user_output_constraints("只回复“好”", parsed)

    assert result.brief == result.detail == "好"


def test_chinese_character_limit_is_a_hard_visible_cap() -> None:
    result = conversation.apply_user_output_constraints(
        "请用一句不超过三十字总结。",
        conversation.TurnResult(
            brief="这是一段明显超过三十个字的摘要内容，模型本来还想继续补充更多细节。",
            detail="更长的详情。",
        ),
    )

    assert len(result.brief) <= 30
    assert result.detail == result.brief


def test_session_title_is_distinguishable_and_elided() -> None:
    assert conversation.make_session_title("2+2 等于几？") == "2+2 等于几？"
    title = conversation.make_session_title("桌面应用本地保存少量配置，SQLite 和 JSON 哪个更合适？")
    assert title.endswith("…")
    assert len(title) == 19


def test_plain_visible_text_removes_compact_markdown_punctuation() -> None:
    assert conversation.plain_visible_text("**答案：** `42`") == "答案： 42"
    assert conversation.plain_visible_text("## 结论\n- 巴黎") == "结论\n巴黎"


def test_parse_turn_result_recovers_mismatched_brief_closing_tag() -> None:
    raw = (
        "【brief】没读到屏——还缺辅助功能权限。【/detail】\n"
        "【detail】请到系统设置开启辅助功能权限。【/detail】"
    )

    result = conversation.parse_turn_result(raw)

    assert result.brief == "没读到屏——还缺辅助功能权限。"
    assert result.detail == "请到系统设置开启辅助功能权限。"
    assert result.fallback_used is True
