"""macOS accessibility helpers: Reduce Motion detection and per-widget screens."""

from __future__ import annotations

import functools

from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QApplication, QWidget


@functools.lru_cache(maxsize=1)
def _reduce_motion_preference() -> bool:
    """Read the user's Reduce Motion preference once per process."""
    try:
        from Foundation import NSUserDefaults
    except ImportError:
        return False
    try:
        defaults = NSUserDefaults.standardUserDefaults()
        return bool(defaults.boolForKey_("reduceMotion"))
    except Exception:  # noqa: BLE001 — any CoreFoundation issue degrades to animations on
        return False


def reduce_motion_enabled() -> bool:
    """True when the user asked macOS to reduce motion (skip decorative animations)."""
    return _reduce_motion_preference()


def screen_of(widget: QWidget | None) -> QScreen:
    """The screen containing the widget (for multi-screen correct placement)."""
    if widget is not None:
        screen = widget.screen()
        if screen is not None:
            return screen
    primary = QApplication.primaryScreen()
    assert primary is not None
    return primary
