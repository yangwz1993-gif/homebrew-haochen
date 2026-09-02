"""Queued input must be visible and cancellable from both entries (task-4b)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import test_session_deletion_ui as harness  # noqa: E402

FakeClient = harness.FakeClient
window_module = harness.window_module
ChatWindow = harness.ChatWindow
widgets = importlib.import_module("haochen_app.chat.widgets")
QueueIndicator = widgets.QueueIndicator


def test_chat_queue_is_visible_and_cancellable_while_foreign_turn_runs(qtbot, monkeypatch) -> None:
    client = FakeClient()
    window = ChatWindow(client)
    qtbot.addWidget(window)

    # 模拟另一入口（气泡）正在生成：本入口消息应进入队列且可见。
    busy_stub = {"value": True}

    class FakeSupervisor:
        coordinator = window.coordinator

        def busy_except(self, _ctrl):
            return busy_stub["value"]

    window._supervisor = FakeSupervisor()

    window.input.setPlainText("排队的问题 A")
    window._on_send()
    window.input.setPlainText("排队的问题 B")
    window._on_send()

    assert window.coordinator.texts("chat") == ["排队的问题 A", "排队的问题 B"]
    indicators = window.findChildren(QueueIndicator)
    assert len(indicators) == 2
    assert indicators[0].label.text().startswith("已排队")
    assert "排队的问题 A" in indicators[0].label.text()

    # 取消第一条：只有它被移除。
    indicators[0].cancel_button.click()
    assert window.coordinator.texts("chat") == ["排队的问题 B"]
    qtbot.waitUntil(lambda: len(window.findChildren(QueueIndicator)) == 1, timeout=1000)

    # 回合结束：剩余队列自动泄流，指示条全部消失。
    busy_stub["value"] = False
    window._on_queue_changed()
    qtbot.waitUntil(
        lambda: window.coordinator.queue and window.coordinator.queue[0].request_id is not None,
        timeout=1000,
    )
    request_id = window.coordinator.queue[0].request_id
    client.response.emit({"id": request_id, "success": True, "type": "response"})
    getattr(client, "event").emit({"type": "message_end", "message": {"role": "user"}})
    qtbot.waitUntil(lambda: not window.coordinator.queue, timeout=1000)
    qtbot.waitUntil(lambda: window.findChildren(QueueIndicator) == [], timeout=1000)


def test_pet_queue_is_visible_and_cancellable(qtbot) -> None:
    pet_module = importlib.import_module("haochen_app.pet.app")
    client = FakeClient()

    class FakeCtrl:
        busy = True

        def send(self, _text):
            return None

    pet = pet_module.PetApp(client=client, supervisor=None)
    qtbot.addWidget(pet.bubble)
    pet.ctrl = FakeCtrl()

    pet.send("气泡排队的问题")

    assert pet.coordinator.texts("pet") == ["气泡排队的问题"]
    qtbot.waitUntil(lambda: bool(pet.bubble.findChildren(QueueIndicator)), timeout=1000)

    indicator = pet.bubble.findChildren(QueueIndicator)[-1]
    indicator.cancel_button.click()

    assert pet.coordinator.queue == ()
