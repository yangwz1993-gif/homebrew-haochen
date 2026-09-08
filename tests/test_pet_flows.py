"""PetApp user flows: state machine, send/abort/retry, confirm routing."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from PyQt6.QtWidgets import QLabel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import test_session_deletion_ui as harness  # noqa: E402

pet_module = importlib.import_module("haochen_app.pet.app")
conversation_module = importlib.import_module("haochen_app.conversation")
PetApp = pet_module.PetApp
PetState = pet_module.PetState


def make_pet(qtbot, tmp_path: Path):
    client = harness.FakeClient()
    client.home = tmp_path
    pet = PetApp(client=client, supervisor=None)
    qtbot.addWidget(pet.bubble)
    qtbot.addWidget(pet.pet)
    return pet, client


def user_message(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def assistant_message(text: str) -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def test_send_runs_single_turn_and_shows_summary(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()

    pet.send("你好")
    assert pet.ctrl.busy
    assert pet.state is PetState.ACKNOWLEDGING
    from haochen_app.chat.widgets import QueueIndicator
    assert not pet.bubble.findChildren(QueueIndicator)

    request_id = pet.ctrl._answer_request_id
    client.response.emit({"id": request_id, "success": True, "type": "response"})
    getattr(client, "event").emit({"type": "message_end", "message": {"role": "user"}})
    getattr(client, "event").emit({"type": "agent_end", "messages": [
        user_message("你好"),
        assistant_message("【brief】短结论【/brief】\n【detail】详答内容【/detail】"),
    ]})

    assert not pet.ctrl.busy
    assert pet.state is PetState.PRESENTING
    from haochen_app.pet.bubble import SummaryBlock  # noqa: E402
    blocks = pet.bubble.findChildren(SummaryBlock)
    assert blocks
    assert blocks[-1].continue_button.text() == "继续问"
    assert blocks[-1].expand_button.text() == "查看详情"
    assert not pet.bubble._input_visible()
    assert pet._result_timer.isActive()


def test_result_continue_restores_only_input(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet._on_summary_done("问题在依赖版本。")

    from haochen_app.pet.bubble import SummaryBlock  # noqa: E402
    block = pet.bubble.findChildren(SummaryBlock)[-1]
    block.continue_button.click()

    assert pet.bubble._input_visible()
    assert not pet.bubble.findChildren(SummaryBlock)
    assert not pet._result_timer.isActive()


def test_continue_cancels_an_inflight_auto_dismiss(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet._on_summary_done("已经处理好了。")
    pet.bubble.dismiss()  # 模拟 8 秒计时器刚进入 150ms 退场阶段

    pet._on_continue()
    qtbot.wait(250)

    assert pet.bubble.summoned
    assert pet.bubble.isVisible()
    assert pet.bubble._input_visible()


def test_result_auto_dismisses_without_interaction(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    pet._result_timer.setInterval(20)
    pet.bubble.summon()
    pet._on_summary_done("已经处理好了。")

    qtbot.waitUntil(lambda: pet.state is PetState.IDLE, timeout=1000)
    assert not pet.bubble.summoned
    assert pet.state is PetState.IDLE


def test_destroying_bubble_stops_result_timer(qtbot, tmp_path: Path) -> None:
    """CI 慢机回归：清理窗口后，自动退场不得访问已删除的 Qt 子控件。"""
    client = harness.FakeClient()
    client.home = tmp_path
    pet = PetApp(client=client, supervisor=None)
    qtbot.addWidget(pet.pet)
    pet._result_timer.setInterval(20)
    pet._result_timer.start()

    pet.bubble.deleteLater()
    qtbot.wait(60)

    assert not pet._result_timer.isActive()


def test_abort_stops_round_and_keeps_state_visible(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet.send("长问题")
    assert pet.ctrl.busy

    pet.abort()

    assert pet._aborted is True
    assert pet.state is PetState.CANCELLED


def test_cancelled_result_says_it_only_contains_completed_content(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet._aborted = True

    pet._on_summary_done("已经完成的半段答案")

    assert pet.state is PetState.CANCELLED
    labels = " ".join(label.text() for label in pet.bubble.findChildren(QLabel))
    assert "已停止" in labels
    assert "停止前已完成的内容" in labels


def test_retry_resends_pending_review_item(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    item = pet.coordinator.enqueue("失败的问题", "pet")
    pet.coordinator.mark_inflight(item.id, "req-x")
    pet.coordinator.hold_for_review("req-x")
    pet._last_user_text = "失败的问题"

    pet._on_retry()

    qtbot.waitUntil(
        lambda: any(i.id == item.id and not i.needs_review for i in pet.coordinator.queue)
        or not pet.coordinator.queue,
        timeout=1000,
    )


def test_engine_event_routes_confirm_to_initiator(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet.ctrl._phase = "answer"

    getattr(client, "event").emit({
        "type": "extension_ui_request",
        "id": "ui-1",
        "method": "confirm",
        "title": "读屏",
        "message": "确认？",
    })
    assert pet._confirm_id == "ui-1"

    # 回答确认
    pet._resolve_confirm(confirmed=True)
    assert pet._confirm_id is None
    pet.ctrl._phase = ""


def test_crash_without_supervisor_shows_error_and_summons(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.dismiss()
    client.crashed.emit(7)
    from haochen_app.pet.bubble import ErrorBlock  # noqa: E402
    assert pet.bubble.findChildren(ErrorBlock)


def test_failure_replaces_working_status_with_human_error(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet.send("你好")
    pet._on_failed("Header 'Authorization' has invalid value 'Bearer sk-secret'")

    from haochen_app.pet.bubble import ErrorBlock, StatusBlock  # noqa: E402
    assert not pet.bubble.findChildren(StatusBlock)
    errors = pet.bubble.findChildren(ErrorBlock)
    assert len(errors) == 1
    text = " ".join(label.text() for label in errors[0].findChildren(QLabel))
    assert "API Key 无效" in text
    assert "sk-secret" not in text


def test_state_transitions_and_pose(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    assert pet.state is PetState.IDLE
    pet._set_state(PetState.LISTENING)
    assert pet.state is PetState.LISTENING
    pet._set_state(PetState.ACKNOWLEDGING)
    assert pet.state is PetState.ACKNOWLEDGING
    pet._set_state(PetState.PERCEIVING)
    assert pet.state is PetState.PERCEIVING
    pet._set_state(PetState.ACTING)
    assert pet.state is PetState.ACTING
    pet._set_state(PetState.COMPOSING)
    assert pet.state is PetState.COMPOSING
    pet._set_state(PetState.PRESENTING)
    assert pet.state is PetState.PRESENTING


def test_engine_events_drive_visible_work_phases(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet.send("帮我处理一下")
    assert pet.state is PetState.ACKNOWLEDGING
    assert pet._status_block.stop_button.isVisible()

    request_id = pet.ctrl._answer_request_id
    client.response.emit({"id": request_id, "success": True, "type": "response"})
    assert pet.state is PetState.COMPOSING
    assert pet._status_block.text == "正在组织回答"

    client.event.emit({"type": "tool_execution_start", "toolName": "bash"})
    assert pet.state is PetState.ACTING
    assert pet._status_block.text == "正在执行命令"

    client.event.emit({"type": "tool_execution_end", "toolName": "bash"})
    assert pet.state is PetState.COMPOSING


def test_transient_status_stop_is_available_after_input_hides(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    aborts: list[bool] = []
    monkeypatch.setattr(client, "abort", lambda: aborts.append(True) or "abort-test")
    pet.bubble.summon()
    pet.bubble.input.setPlainText("一个比较慢的问题")
    pet.bubble._on_send()

    assert not pet.bubble._input_visible()
    assert pet._status_block.stop_button.isVisible()
    pet._status_block.stop_button.click()

    assert pet._aborted is True
    assert aborts == [True]
    assert not pet._status_block.stop_button.isVisible()


def test_status_block_has_comic_pulse_without_exposing_reasoning(qtbot) -> None:
    from haochen_app.pet.bubble import StatusBlock  # noqa: E402

    block = StatusBlock("正在组织回答")
    qtbot.addWidget(block)
    block._pulse.setInterval(10)
    before = block._lb.text()
    qtbot.waitUntil(lambda: block._lb.text() != before, timeout=250)

    assert block.text == "正在组织回答"
    assert " ·" in block._lb.text()
    block.set_text("想好了 ✓", animated=False)
    assert not block._pulse.isActive()


def test_bubble_toggle_and_escape(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    assert not pet.bubble.summoned
    pet._toggle_bubble()
    assert pet.bubble.summoned
    pet._on_escape()
    assert not pet.bubble.summoned


def test_name_reply_is_persisted_not_sent(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet._awaiting_name = True
    sent: list[str] = []
    original_send = pet.send
    pet.send = lambda text: sent.append(text)

    pet._handle_name_reply("阿晨")

    assert sent == []  # 称呼不进引擎
    pet.send = original_send
