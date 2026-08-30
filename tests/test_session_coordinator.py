from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
coordinator_module = importlib.import_module("haochen_app.session_coordinator")
SessionCoordinator = coordinator_module.SessionCoordinator
EngineSupervisor = importlib.import_module("haochen_app.supervisor").EngineSupervisor


def mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_current_session_and_queue_survive_restart_for_explicit_review(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = SessionCoordinator(home)
    first.set_current_session("/sessions/B.jsonl")
    item = first.enqueue("recover this message", "chat")
    first.mark_inflight(item.id, "request-1")

    restored = SessionCoordinator(home)

    assert restored.current_session == "/sessions/B.jsonl"
    assert restored.texts() == ["recover this message"]
    recovered = restored.queue[0]
    assert recovered.request_id is None
    assert recovered.needs_review is True
    assert restored.next_ready("chat") is None
    assert mode(home) == 0o700
    assert mode(restored.state_path) == 0o600

    restored.retry(recovered.id)
    ready = restored.next_ready("chat")
    assert ready and ready.id == recovered.id
    restored.mark_inflight(ready.id, "request-2")
    assert restored.acknowledge("request-2") is not None
    assert restored.queue == ()


def test_queue_preserves_cross_entry_order_and_cancel(tmp_path: Path) -> None:
    coordinator = SessionCoordinator(tmp_path / "home")
    chat = coordinator.enqueue("chat first", "chat")
    pet = coordinator.enqueue("pet second", "pet")

    assert coordinator.next_ready("pet") is None
    assert coordinator.next_ready("chat") == chat
    coordinator.cancel(chat.id)
    assert coordinator.next_ready("pet") == pet
    coordinator.cancel(pet.id)
    assert coordinator.queue == ()


def test_failed_inflight_message_requires_review(tmp_path: Path) -> None:
    coordinator = SessionCoordinator(tmp_path / "home")
    item = coordinator.enqueue("do not lose me", "pet")
    coordinator.mark_inflight(item.id, "request")

    held = coordinator.hold_for_review("request")

    assert held is not None and held.needs_review
    assert coordinator.next_ready("pet") is None
    reloaded = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    assert reloaded["queue"][0]["text"] == "do not lose me"


def test_supervisor_restores_latest_coordinator_session_after_crash(tmp_path: Path, monkeypatch) -> None:
    supervisor = EngineSupervisor(mock=True, home=tmp_path / "home")
    supervisor.coordinator.set_current_session("mock-session://A.jsonl")
    supervisor.coordinator.set_current_session("mock-session://B.jsonl")
    switched: list[str] = []

    def switch_session(path: str) -> str:
        switched.append(path)
        return "switch-request"

    monkeypatch.setattr(supervisor.client, "switch_session", switch_session)
    supervisor._restarting = True
    supervisor._probe_ok(
        {
            "success": True,
            "data": {"sessionFile": "mock-session://fresh.jsonl"},
        }
    )

    assert switched == ["mock-session://B.jsonl"]
    supervisor._on_response(
        {
            "id": "switch-request",
            "success": True,
            "data": {"cancelled": False},
        }
    )
    assert supervisor._restarting is False
    assert supervisor.coordinator.current_session == "mock-session://B.jsonl"


def test_corrupt_runtime_state_is_backed_up_not_silently_destroyed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    state = home / "runtime-state.json"
    state.write_text('{"queue":[not valid', encoding="utf-8")

    coordinator = SessionCoordinator(home)

    backups = list(home.glob("runtime-state.json.corrupt*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"queue":[not valid'
    assert mode(backups[0]) == 0o600
    assert coordinator.queue == ()


def test_symlink_runtime_state_is_removed_without_touching_target(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "outside"
    target.write_text("outside", encoding="utf-8")
    (home / "runtime-state.json").symlink_to(target)

    coordinator = SessionCoordinator(home)

    assert target.read_text(encoding="utf-8") == "outside"
    assert not coordinator.state_path.is_symlink()
    assert json.loads(coordinator.state_path.read_text(encoding="utf-8"))["queue"] == []


def test_hold_item_and_missing_request_operations(tmp_path: Path) -> None:
    coordinator = SessionCoordinator(tmp_path / "home")
    item = coordinator.enqueue("hello", "chat")
    held = coordinator.hold_item_for_review(item.id)
    assert held.needs_review
    assert coordinator.acknowledge("missing") is None
    assert coordinator.hold_for_review("missing") is None


def test_invalid_queue_inputs_are_rejected(tmp_path: Path) -> None:
    coordinator = SessionCoordinator(tmp_path / "home")
    with pytest.raises(ValueError):
        coordinator.enqueue("", "chat")
    with pytest.raises(ValueError):
        coordinator.enqueue("hello", "unknown")
    with pytest.raises(KeyError):
        coordinator.cancel("missing")
