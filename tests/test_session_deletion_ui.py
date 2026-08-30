from __future__ import annotations

import importlib
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

window_module = importlib.import_module("haochen_app.chat.window")
ChatWindow = window_module.ChatWindow
ActionBanner = window_module.ActionBanner
ErrorBanner = window_module.ErrorBanner


class FakeClient(QObject):
    event = pyqtSignal(dict)  # pyright: ignore[reportAssignmentType]
    response = pyqtSignal(dict)
    crashed = pyqtSignal(int)

    def __init__(self, mock: bool = True) -> None:
        super().__init__()
        self._mock = mock
        self._next = 0
        self.home = Path("/tmp/haochen-ui-test")

    @property
    def alive(self) -> bool:
        return True

    def _id(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next}"

    def new_session(self) -> str:
        return self._id("new")

    def get_state(self) -> str:
        return self._id("state")

    def get_messages(self) -> str:
        return self._id("messages")

    def switch_session(self, _path: str) -> str:
        return self._id("switch")

    def respond_ui(self, *_args, **_kwargs) -> str:
        return self._id("ui")


def accept_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        window_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )


def make_window(qtbot, *, mock: bool = True):
    window = ChatWindow(FakeClient(mock=mock))
    qtbot.addWidget(window)
    window._sessions = [
        {"path": "/sessions/a.jsonl", "title": "A"},
        {"path": "/sessions/b.jsonl", "title": "B"},
    ]
    window._current_path = "/sessions/a.jsonl"
    window._refresh_sidebar()
    return window


def test_delete_requires_confirmation(qtbot, monkeypatch) -> None:
    window = make_window(qtbot)
    monkeypatch.setattr(
        window_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    window._delete_session("/sessions/b.jsonl")

    assert [session["title"] for session in window._sessions] == ["A", "B"]
    assert not window.findChildren(ActionBanner)


def test_delete_failure_is_visible_and_keeps_session(qtbot, monkeypatch) -> None:
    window = make_window(qtbot, mock=False)
    accept_confirmation(monkeypatch)

    def fail_delete(_path: str, _home: Path):
        raise PermissionError("permission denied")

    monkeypatch.setattr(window_module, "delete_session", fail_delete)
    window._delete_session("/sessions/b.jsonl")

    assert [session["title"] for session in window._sessions] == ["A", "B"]
    assert window.findChildren(ErrorBanner)
    assert "原会话仍保留" in window.sidebar.status.text()


def test_deleted_session_can_be_undone(qtbot, monkeypatch) -> None:
    window = make_window(qtbot)
    accept_confirmation(monkeypatch)

    window._delete_session("/sessions/b.jsonl")
    assert [session["title"] for session in window._sessions] == ["A"]

    banner = window.findChildren(ActionBanner)[-1]
    banner.action_button.click()

    assert [session["title"] for session in window._sessions] == ["A", "B"]
    assert "已恢复" in banner.message.text()
    assert banner.action_button.isHidden()


def respond_to_new_session(window, state_path: str) -> None:
    new_request = next(request_id for request_id in window._pending_rpc if request_id.startswith("new-"))
    window._on_response({"id": new_request, "success": True, "data": {}})
    state_request = next(request_id for request_id in window._pending_rpc if request_id.startswith("state-"))
    window._on_response(
        {
            "id": state_request,
            "success": True,
            "data": {"sessionFile": state_path},
        }
    )


def test_current_session_is_not_deleted_until_new_session_is_confirmed(qtbot, monkeypatch) -> None:
    window = make_window(qtbot)
    accept_confirmation(monkeypatch)

    window._delete_session("/sessions/a.jsonl")
    respond_to_new_session(window, "/sessions/a.jsonl")

    assert [session["title"] for session in window._sessions] == ["A", "B"]
    assert window.findChildren(ErrorBanner)


def test_current_session_is_deleted_after_switch_is_confirmed(qtbot, monkeypatch) -> None:
    window = make_window(qtbot)
    accept_confirmation(monkeypatch)

    window._delete_session("/sessions/a.jsonl")
    respond_to_new_session(window, "/sessions/new.jsonl")

    assert [session["title"] for session in window._sessions] == ["新会话", "B"]
    assert window._current_path == "/sessions/new.jsonl"
    assert window.findChildren(ActionBanner)


def test_current_session_delete_failure_switches_engine_back(qtbot, monkeypatch) -> None:
    window = make_window(qtbot, mock=False)
    accept_confirmation(monkeypatch)
    monkeypatch.setattr(
        window_module,
        "delete_session",
        lambda _path, _home: (_ for _ in ()).throw(PermissionError("permission denied")),
    )

    window._delete_session("/sessions/a.jsonl")
    respond_to_new_session(window, "/sessions/new.jsonl")

    assert window._current_path == "/sessions/a.jsonl"
    assert [session["title"] for session in window._sessions] == ["A", "B"]
    assert any(request_id.startswith("switch-") for request_id in window._pending_rpc)
