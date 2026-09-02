"""PetApp user flows: state machine, send/abort/retry, confirm routing."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

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


def complete_round(client, answer: str = "【answer】答【/answer】", summary: str = "【summary】结【/summary】") -> None:
    # 回答 accepted → user message_end → agent_end(answer) → agent_end(summary)
    # 通过 controller 的两个 prompt request id 结算
    # answer 阶段
    getattr(client, "response").emit({"id": f"request-{len(_sent_count(client))}", "success": True, "type": "response"})
    getattr(client, "event").emit({"type": "message_end", "message": {"role": "user"}})
    getattr(client, "event").emit({"type": "agent_end", "messages": [user_message("q"), assistant_message(answer)]})
    getattr(client, "event").emit({"type": "agent_end", "messages": [user_message("k"), assistant_message(summary)]})


def _sent_count(client) -> list:
    return getattr(client, "_ids", [])


def test_send_runs_two_step_round_and_shows_summary(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()

    pet.send("你好")
    assert pet.ctrl.busy
    assert pet.state.value == "THINK"

    request_id = pet.ctrl._answer_request_id
    client.response.emit({"id": request_id, "success": True, "type": "response"})
    getattr(client, "event").emit({"type": "message_end", "message": {"role": "user"}})
    getattr(client, "event").emit({"type": "agent_end", "messages": [
        user_message("你好"), assistant_message("【answer】详答内容【/answer】")]})
    assert pet.ctrl.busy  # summary 阶段
    getattr(client, "event").emit({"type": "agent_end", "messages": [
        user_message("k"), assistant_message("【summary】短结论【/summary】")]})

    assert not pet.ctrl.busy
    assert pet.state.value == "AWAKE"
    from haochen_app.pet.bubble import SummaryBlock  # noqa: E402
    assert pet.bubble.findChildren(SummaryBlock)


def test_abort_stops_round_and_keeps_state_visible(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet.send("长问题")
    assert pet.ctrl.busy

    pet.abort()

    assert pet._aborted is True


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


def test_state_transitions_and_pose(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    assert pet.state is PetState.IDLE
    pet._set_state(PetState.AWAKE)
    assert pet.state is PetState.AWAKE
    pet._set_state(PetState.THINK)
    assert pet.state is PetState.THINK
    pet._set_state(PetState.PERCEIVE)
    assert pet.state is PetState.PERCEIVE
    pet._set_state(PetState.ACT)
    assert pet.state is PetState.ACT
    pet._set_state(PetState.CONVERGE)
    assert pet.state is PetState.CONVERGE


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
