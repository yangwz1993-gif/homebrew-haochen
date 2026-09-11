"""Single source of truth for the active session and crash-safe user-message queue."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from .app_tracking import capture_question_target
from .secure_storage import atomic_write_private, ensure_private_directory, ensure_private_file


@dataclass
class QueueItem:
    id: str
    text: str
    source: str
    request_id: str | None = None
    needs_review: bool = False
    screen_target: dict | None = None


class SessionCoordinator(QObject):
    session_changed = pyqtSignal(str)
    queue_changed = pyqtSignal(list)

    def __init__(self, home: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.home = Path(home)
        self.state_path = self.home / "runtime-state.json"
        self.current_session: str | None = None
        self._queue: list[QueueItem] = []
        self._load()

    @property
    def queue(self) -> tuple[QueueItem, ...]:
        return tuple(self._queue)

    def _load(self) -> None:
        ensure_private_directory(self.home)
        if not self.state_path.exists():
            return
        try:
            ensure_private_file(self.state_path)
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            current = data.get("currentSession")
            self.current_session = current if isinstance(current, str) and current else None
            for raw in data.get("queue", []):
                if not isinstance(raw, dict):
                    continue
                text, source = raw.get("text"), raw.get("source")
                if not isinstance(text, str) or not text or source not in {"chat", "pet"}:
                    continue
                self._queue.append(
                    QueueItem(
                        id=str(raw.get("id") or uuid.uuid4().hex),
                        text=text,
                        source=source,
                        request_id=None,
                        # After process death, never auto-resend a message whose
                        # acceptance is unknown. The user explicitly retries/cancels.
                        needs_review=True,
                    )
                )
        except (OSError, ValueError, json.JSONDecodeError):
            self.current_session = None
            self._queue = []
            if self.state_path.is_symlink():
                self.state_path.unlink()
            elif self.state_path.exists():
                backup = self.state_path.with_suffix(".json.corrupt")
                sequence = 1
                while backup.exists():
                    backup = self.state_path.with_suffix(f".json.corrupt.{sequence}")
                    sequence += 1
                self.state_path.replace(backup)
                backup.chmod(0o600)
        self._persist()

    def _persist(self) -> None:
        payload = {
            "currentSession": self.current_session,
            "queue": [asdict(item) for item in self._queue],
        }
        atomic_write_private(
            self.state_path,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        )

    def set_current_session(self, path: str | None) -> None:
        normalized = path or None
        if normalized == self.current_session:
            return
        self.current_session = normalized
        self._persist()
        self.session_changed.emit(normalized or "")

    def enqueue(self, text: str, source: str) -> QueueItem:
        text = text.strip()
        if not text:
            raise ValueError("queued message must not be empty")
        if source not in {"chat", "pet"}:
            raise ValueError("unknown queue source")
        item = QueueItem(id=uuid.uuid4().hex, text=text, source=source,
                         screen_target=capture_question_target(self.home))
        self._queue.append(item)
        self._persist()
        self.queue_changed.emit(list(self._queue))
        return item

    def next_ready(self, source: str) -> QueueItem | None:
        if not self._queue:
            return None
        item = self._queue[0]
        if item.source != source or item.request_id is not None or item.needs_review:
            return None
        return item

    def mark_inflight(self, item_id: str, request_id: str) -> None:
        item = self._find(item_id)
        item.request_id = request_id
        item.needs_review = False
        self._persist()
        self.queue_changed.emit(list(self._queue))

    def acknowledge(self, request_id: str) -> QueueItem | None:
        for index, item in enumerate(self._queue):
            if item.request_id == request_id:
                removed = self._queue.pop(index)
                self._persist()
                self.queue_changed.emit(list(self._queue))
                return removed
        return None

    def hold_for_review(self, request_id: str) -> QueueItem | None:
        for item in self._queue:
            if item.request_id == request_id:
                item.request_id = None
                item.needs_review = True
                self._persist()
                self.queue_changed.emit(list(self._queue))
                return item
        return None

    def hold_item_for_review(self, item_id: str) -> QueueItem:
        item = self._find(item_id)
        item.request_id = None
        item.needs_review = True
        self._persist()
        self.queue_changed.emit(list(self._queue))
        return item

    def retry(self, item_id: str) -> None:
        item = self._find(item_id)
        item.request_id = None
        item.needs_review = False
        self._persist()
        self.queue_changed.emit(list(self._queue))

    def cancel(self, item_id: str) -> QueueItem:
        item = self._find(item_id)
        self._queue.remove(item)
        self._persist()
        self.queue_changed.emit(list(self._queue))
        return item

    def texts(self, source: str | None = None) -> list[str]:
        return [item.text for item in self._queue if source is None or item.source == source]

    def _find(self, item_id: str) -> QueueItem:
        for item in self._queue:
            if item.id == item_id:
                return item
        raise KeyError(item_id)
