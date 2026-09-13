"""Public notch geometry and status normalization without external UI access."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
notch = importlib.import_module("haochen_app.dashboard.notch")
native = importlib.import_module("haochen_app.dashboard.native_window")


def test_notch_entrance_is_attached_below_not_over_menu_or_camera():
    frame, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 76, 1470, 847), 32,
        (0, 924, 646, 32), (825, 924, 645, 32))
    assert attached
    assert frame == (646, 888, 179, 36)
    assert frame[1] + frame[3] == 924  # exactly the notch's lower edge
    assert frame[0] + frame[2] == 825  # no side menu items are intercepted


@pytest.mark.parametrize("origin", [(-1920, 0), (0, 956), (1470, -200)])
def test_external_display_capsule_stays_within_its_own_visible_frame(origin):
    x, y = origin
    frame, attached = notch.entrance_geometry((x, y, 1920, 1080), (x, y, 1920, 1055))
    assert not attached
    assert x <= frame[0] < frame[0] + frame[2] <= x + 1920
    assert y <= frame[1] < frame[1] + frame[3] < y + 1055


def test_hidden_menu_does_not_expand_hit_area_over_notch():
    frame, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 0, 1470, 956), 32,
        (0, 924, 646, 32), (825, 924, 645, 32))
    assert attached
    assert frame[1] + frame[3] <= 924


def test_empty_or_invalid_auxiliary_gap_falls_back_to_capsule():
    frame, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 0, 1470, 924), 32,
        (0, 0, 0, 0), (0, 0, 0, 0))
    assert not attached
    assert frame[1] + frame[3] < 924


def test_activity_never_invents_unread_count_or_status():
    assert notch.activity_from(None) == notch.Activity()
    assert notch.activity_from({"kind": "fake", "count": "3"}) == notch.Activity()
    assert notch.activity_from({"kind": [], "label": None}) == notch.Activity()
    activity = notch.activity_from({"kind": "new", "count": 3, "label": "有新结果"})
    assert (activity.kind, activity.count, activity.label) == ("new", 3, "有新结果")
    assert notch.activity_from({"kind": "attention", "count": -1}).count == 0
    assert notch.activity_from({"kind": "error", "count": True}).count == 0
    assert notch.activity_from({"kind": "running", "count": 1200}).count == 999


def test_command_m_does_not_consume_modified_or_unrelated_commands():
    assert notch.command_m("m", 1, 1, 2, 4, 8)
    assert notch.command_m("M", 17, 1, 2, 4, 8)  # Caps Lock ignored
    for char, flags in [("m", 0), ("m", 3), ("m", 5), ("m", 9), ("n", 1), (None, 1)]:
        assert not notch.command_m(char, flags, 1, 2, 4, 8)


def test_main_dashboard_never_uses_floating_all_spaces_window():
    source = (Path(__file__).resolve().parents[1]
              / "app/haochen_app/dashboard/native_window.py").read_text()
    assert "setLevel_(AK.NSNormalWindowLevel)" in source
    assert "NSFloatingWindowLevel" not in source
    assert "NSWindowCollectionBehaviorCanJoinAllSpaces" not in source
    assert "QCursor" not in source


def test_anchor_screen_is_stable_until_removed(monkeypatch):
    class Screen:
        def __init__(self, identifier, inset):
            self.identifier, self.inset = identifier, inset

        def deviceDescription(self):
            return {"NSScreenNumber": self.identifier}

        def respondsToSelector_(self, _selector):
            return True

        def safeAreaInsets(self):
            return SimpleNamespace(top=self.inset)

    builtin, external = Screen(1, 32), Screen(2, 0)
    screens = [external, builtin]
    api = SimpleNamespace(screens=lambda: screens, mainScreen=lambda: screens[0])
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSScreen=api))
    owner = SimpleNamespace(_screen_id=None)
    assert native.NativeDashboard._ns_screen(owner) is builtin
    assert owner._screen_id == 1
    assert native.NativeDashboard._ns_screen(owner) is builtin  # does not follow main/cursor
    screens.remove(builtin)
    assert native.NativeDashboard._ns_screen(owner) is external
    assert owner._screen_id == 2
    screens.append(builtin)
    assert native.NativeDashboard._ns_screen(owner) is external  # no teleport on reconnect
