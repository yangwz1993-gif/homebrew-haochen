"""Run bounded I/O away from Qt's GUI thread and deliver results to its owner."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, pyqtSignal


class BackgroundJob(QObject):
    finished = pyqtSignal(object, object)

    def __init__(self, owner: QObject, work: Callable, done: Callable):
        super().__init__(owner)
        self._work = work
        self._done = done
        self.finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        threading.Thread(target=self._run, name="haochen-config-io", daemon=True).start()

    def _run(self) -> None:
        result, error = None, None
        try:
            result = self._work()
        except Exception as exc:  # noqa: BLE001 - always release the UI on failure
            error = exc
        try:
            self.finished.emit(result, error)
        except RuntimeError:
            pass  # The owning window/application was destroyed while I/O finished.

    def _deliver(self, result, error) -> None:
        try:
            self._done(result, error)
        finally:
            self._work = self._done = None
            self.deleteLater()


def run_in_background(owner: QObject, work: Callable, done: Callable) -> None:
    BackgroundJob(owner, work, done).start()
