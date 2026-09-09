"""PetApp user flows: state machine, send/abort/retry, confirm routing."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
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
        assistant_message("【brief】**短结论**【/brief】\n【detail】详答内容【/detail】"),
    ]})

    assert not pet.ctrl.busy
    assert pet.state is PetState.PRESENTING
    from haochen_app.pet.bubble import SummaryBlock  # noqa: E402
    blocks = pet.bubble.findChildren(SummaryBlock)
    assert blocks
    assert blocks[-1].continue_button.text() == "继续问"
    assert blocks[-1].expand_button.text() == "查看详情"
    visible = " ".join(label.text() for label in blocks[-1].findChildren(QLabel))
    assert "短结论" in visible
    assert "**" not in visible
    assert not pet.bubble._input_visible()
    assert pet._result_timer.isActive()


def test_unstructured_exact_reply_still_shows_a_transient_result(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet.send("只回复“好”")
    request_id = pet.ctrl._answer_request_id
    client.response.emit({"id": request_id, "success": True, "type": "response"})
    getattr(client, "event").emit({"type": "message_end", "message": {"role": "user"}})
    getattr(client, "event").emit({"type": "agent_end", "messages": [
        user_message("只回复“好”"), assistant_message("好"),
    ]})

    from haochen_app.pet.bubble import SummaryBlock  # noqa: E402
    blocks = pet.bubble.findChildren(SummaryBlock)
    assert blocks
    assert "好" in " ".join(label.text() for label in blocks[-1].findChildren(QLabel))
    assert pet.bubble.summoned
    assert pet._result_timer.isActive()


def test_first_pet_message_names_new_session(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    names: list[str] = []
    client.set_session_name = lambda name: names.append(name) or "name-1"

    pet.send("2+2 等于几？只给答案。")
    assert names == []  # 会话文件尚未由首条 user message 创建
    request_id = pet.ctrl._answer_request_id
    client.response.emit({"id": request_id, "success": True})
    getattr(client, "event").emit({"type": "message_end", "message": {"role": "user"}})

    assert names == ["2+2 等于几？只给答案。"]
    assert pet._session_needs_title is False


def test_result_timer_uses_precise_twenty_second_dwell(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)

    assert pet._result_timer.timerType() is Qt.TimerType.PreciseTimer
    assert pet._result_timer.interval() == 20_000


def test_pending_pet_title_retries_after_session_path_is_confirmed(qtbot, tmp_path: Path) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    names: list[str] = []
    client.set_session_name = lambda name: names.append(name) or f"name-{len(names)}"
    pet._pending_session_title = "彩虹为什么出现"
    pet._session_needs_title = True

    pet._on_session_state({"sessionFile": "/sessions/new.jsonl", "sessionName": "新会话"})

    assert names == ["彩虹为什么出现"]
    assert pet._session_needs_title is False


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


def test_success_result_cancels_any_stale_dismiss_animation(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet.bubble.dismiss()

    pet._on_summary_done("答案已经生成。")
    qtbot.wait(250)

    assert pet.bubble.summoned
    assert pet.bubble.isVisible()
    assert pet.state is PetState.PRESENTING


def test_late_detail_collapse_does_not_hide_reopened_bubble(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet._detail_open = True
    pet.bubble.hide()  # 打开详情时保留 summoned 标记的隐藏状态
    pet.bubble.start_input()  # 用户已重新开始一次可见交互

    pet.restore_bubble()  # 较晚到达的详情收起回调
    qtbot.wait(250)

    assert pet.bubble.summoned
    assert pet.bubble.isVisible()
    assert pet.bubble._input_visible()


def test_detail_hides_topmost_pet_until_collapsed(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    opened: list = []
    pet.detail_opener = lambda rect: opened.append(rect)
    pet.pet.show()
    pet.bubble.summon(show_input=False)
    pet._last_summary = "结论"

    pet._on_expand_detail()

    assert opened
    assert not pet.pet.isVisible()
    pet.restore_bubble()
    assert pet.pet.isVisible()


def test_short_results_survive_repeated_detail_close_races(qtbot, tmp_path: Path) -> None:
    """回归 B1：20 次详情收起竞态 + 50 次短结果均必须留在桌面层。"""
    pet, _client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()

    for index in range(50):
        if index < 20:
            pet._detail_open = True
            pet.bubble.hide()
            pet.bubble.start_input()
            pet.restore_bubble()
        elif index % 2:
            pet.bubble.dismiss()
        pet._on_summary_done(f"短答 {index}")
        assert pet.bubble.summoned
        assert pet.bubble.isVisible()
        assert pet.state is PetState.PRESENTING
        pet._on_continue()


def test_result_auto_dismisses_without_interaction(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    pet._result_timer.setInterval(20)
    pet.bubble.summon()
    pet._on_summary_done("已经处理好了。")

    qtbot.waitUntil(lambda: pet.state is PetState.IDLE, timeout=1000)
    assert not pet.bubble.summoned
    assert pet.state is PetState.IDLE


def test_default_result_dwell_is_long_enough_to_read() -> None:
    assert pet_module.RESULT_AUTO_DISMISS_MS >= 18_000


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
    assert "停止前生成的内容已经保留" in labels


def test_invalid_key_failure_reports_runtime_validation(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    reported: list[tuple[bool, str]] = []
    pet.credential_validation.connect(lambda ok, message: reported.append((ok, message)))

    pet._on_failed("Header 'Authorization' has invalid value")

    assert reported == [(False, "API Key 无效或格式不正确")]


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
    labels = " ".join(label.text() for label in pet.bubble.findChildren(QLabel))
    assert "正在重试（第 1 次）" in labels

    pet._on_failed("missing API key")
    labels = " ".join(label.text() for label in pet.bubble.findChildren(QLabel))
    assert "刚刚完成第 1 次重试" in labels


def test_edge_clamped_bubble_tail_tracks_pet_center(qtbot, tmp_path: Path) -> None:
    from haochen_app.a11y import screen_of

    pet, _client = make_pet(qtbot, tmp_path)
    screen = screen_of(pet.pet).availableGeometry()
    pet.pet.move(screen.left() + 4, screen.center().y())
    pet.bubble.start_input()

    pet._place_bubble()

    assert pet.bubble.tail_side == "bottom"
    assert abs(pet.bubble.tail_tip_global_x - (pet.pet.x() + pet.pet.width() // 2)) <= 1


def test_top_edge_flips_tail_above_bubble(qtbot, tmp_path: Path) -> None:
    from haochen_app.a11y import screen_of

    pet, _client = make_pet(qtbot, tmp_path)
    screen = screen_of(pet.pet).availableGeometry()
    pet.pet.move(screen.center().x(), screen.top() + 4)
    pet.bubble.start_input()

    pet._place_bubble()

    assert pet.bubble.tail_side == "top"
    assert pet.bubble.y() > pet.pet.y()
    assert abs(pet.bubble.tail_tip_global_x - (pet.pet.x() + pet.pet.width() // 2)) <= 1


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


def test_rejecting_screen_read_immediately_removes_reading_semantics(qtbot, tmp_path: Path) -> None:
    from haochen_app.pet.bubble import HintBlock

    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet.send("看看屏幕")
    getattr(client, "event").emit({"type": "tool_execution_start", "toolName": "read_screen"})
    getattr(client, "event").emit({
        "type": "extension_ui_request",
        "id": "ui-deny",
        "method": "confirm",
        "title": "读屏",
        "message": "确认？",
    })

    permission_requests: list[bool] = []
    pet.read_permission_requested.connect(lambda: permission_requests.append(True))
    qtbot.mouseClick(pet.bubble._confirm_bar.btn_no, Qt.MouseButton.LeftButton)

    assert pet.state is PetState.COMPOSING
    assert pet._status_block.text == "已拒绝，未读取屏幕。正在基于已有信息回答"
    assert not pet.bubble.findChildren(HintBlock)
    assert pet.bubble.isVisible()
    assert permission_requests == []


def test_rejecting_screen_read_keeps_confirmation_visible_before_fast_result(
    qtbot, tmp_path: Path
) -> None:
    from haochen_app.pet.bubble import SummaryBlock

    pet, _client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet._on_confirm_resolved(False)
    pet._on_summary_done("我没有读取屏幕，所以无法判断。")

    qtbot.wait(1_000)
    assert pet.bubble.summoned
    assert pet.bubble.isVisible()
    labels = " ".join(label.text() for label in pet.bubble.findChildren(QLabel))
    assert "未读取屏幕" in labels
    assert not pet.bubble.findChildren(SummaryBlock)

    qtbot.waitUntil(lambda: bool(pet.bubble.findChildren(SummaryBlock)), timeout=1_000)
    assert pet.bubble.summoned
    assert pet.bubble.isVisible()


def test_accepting_screen_read_requests_system_permission_after_confirmation(
    qtbot, tmp_path: Path
) -> None:
    pet, client = make_pet(qtbot, tmp_path)
    pet.bubble.summon()
    pet.ctrl._phase = "answer"
    client.event.emit({
        "type": "extension_ui_request",
        "id": "ui-allow",
        "method": "confirm",
        "title": "读屏",
        "message": "确认？",
    })
    permission_requests: list[bool] = []
    pet.read_permission_requested.connect(lambda: permission_requests.append(True))

    qtbot.mouseClick(pet.bubble._confirm_bar.btn_yes, Qt.MouseButton.LeftButton)

    assert permission_requests == [True]
    assert pet._confirm_id is None
    pet.ctrl._phase = ""


def test_first_use_hint_turns_first_click_into_input(qtbot, tmp_path: Path) -> None:
    pet, _client = make_pet(qtbot, tmp_path)
    pet._discovery_hint_retries = 30  # 测试进程中的其他顶层窗口不应阻止本断言

    pet._maybe_show_discovery_hint()

    assert pet._discovery_hint_active
    assert pet.bubble.summoned
    assert not pet.bubble._input_visible()
    assert not (tmp_path / "interaction-hint-v1").exists()

    pet._toggle_bubble()

    assert not pet._discovery_hint_active
    assert (tmp_path / "interaction-hint-v1").exists()
    assert pet.bubble.summoned
    assert pet_module.DISCOVERY_HINT_MS >= 6_000
    assert "点击开始对话" in pet.pet.accessibleName()
    assert pet.bubble._input_visible()
    assert pet.state is PetState.LISTENING


def test_restarting_feedback_replaces_previous_attempt_instead_of_accumulating(
    qtbot, tmp_path: Path
) -> None:
    from haochen_app.pet.bubble import ErrorBlock, StatusBlock

    pet, _client = make_pet(qtbot, tmp_path)
    pet._on_sup_restarting(1)
    pet._on_sup_restarting(2)
    pet._on_sup_restarting(3)

    assert len(pet.bubble.findChildren(StatusBlock)) == 1
    assert not pet.bubble.findChildren(ErrorBlock)

    pet._on_sup_restart_failed()
    assert len(pet.bubble.findChildren(ErrorBlock)) == 1
    assert not pet.bubble.findChildren(StatusBlock)


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
    assert pet.state is PetState.ACKNOWLEDGING
    qtbot.waitUntil(lambda: pet.state is PetState.COMPOSING, timeout=1000)
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
