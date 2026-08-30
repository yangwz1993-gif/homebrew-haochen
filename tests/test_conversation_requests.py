from __future__ import annotations

import importlib
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
ConversationController = importlib.import_module("haochen_app.conversation").ConversationController


class FakeClient(QObject):
    event = pyqtSignal(dict)  # pyright: ignore[reportAssignmentType]
    response = pyqtSignal(dict)
    crashed = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    def prompt(self, text: str) -> str:
        self.sent.append(text)
        return f"request-{len(self.sent)}"

    def abort(self) -> str:
        return "abort"


def test_prompt_acceptance_is_exposed_for_queue_acknowledgement() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    accepted: list[str] = []
    committed: list[str] = []
    controller.request_accepted.connect(accepted.append)
    controller.request_committed.connect(committed.append)

    request_id = controller.send("hello")
    client.response.emit({"id": request_id, "success": True})

    assert accepted == [request_id]
    assert committed == []
    getattr(client, "event").emit({"type": "message_end", "message": {"role": "user"}})
    assert committed == [request_id]
    assert controller.busy


def test_crash_after_acceptance_before_user_commit_exposes_retryable_request() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    request_failures: list[str] = []
    controller.request_failed.connect(lambda request_id, _error: request_failures.append(request_id))

    request_id = controller.send("hello")
    client.response.emit({"id": request_id, "success": True})
    client.crashed.emit(9)

    assert request_failures == [request_id]
    assert not controller.busy


def test_prompt_timeout_releases_controller_and_exposes_failure() -> None:
    client = FakeClient()
    controller = ConversationController(client)
    request_failures: list[tuple[str, str]] = []
    failures: list[str] = []
    controller.request_failed.connect(lambda request_id, error: request_failures.append((request_id, error)))
    controller.failed.connect(failures.append)

    request_id = controller.send("hello")
    client.response.emit(
        {
            "id": request_id,
            "success": False,
            "errorCode": "timeout",
            "error": "engine request timed out",
        }
    )

    assert request_failures == [(request_id, "engine request timed out")]
    assert failures == ["engine request timed out"]
    assert not controller.busy
