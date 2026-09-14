"""Public notch geometry and status normalization without external UI access."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
notch = importlib.import_module("haochen_app.dashboard.notch")
native = importlib.import_module("haochen_app.dashboard.native_window")


def test_notch_entrance_is_flush_and_seamless_within_the_gap():
    frame, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 76, 1470, 847), 32,
        (0, 924, 646, 32), (825, 924, 645, 32))
    assert attached
    # Flush with the screen top so the pure-black fill is continuous with the
    # hardware notch (it grows downward, not a capsule hanging below).
    assert frame[1] + frame[3] == 956
    assert frame[3] == 32 + notch.NOTCH_DROP  # notch height + label band
    # Stays inside the notch gap: no side menu/status item is covered.
    assert frame[0] == 646 and frame[0] + frame[2] == 825


def test_attached_entrance_is_pure_black_to_match_the_notch():
    # A near-black fill is the seam users report; attached must be pure black
    # so it is the same material as the hardware notch.
    assert notch.entrance_fill(True) == (0.0, 1.0)
    assert notch.entrance_fill(True, highlighted=True)[1] == 1.0
    # The free-floating capsule stays a visible dark chip over any wallpaper.
    white, alpha = notch.entrance_fill(False)
    assert white > 0 and alpha < 1.0


def test_attached_corner_radius_is_tighter_than_floating_capsule():
    assert notch.entrance_corner_radius(True) < notch.entrance_corner_radius(False)


def test_geometry_is_snapped_to_the_pixel_grid():
    # Fractional screen metrics must not leave an antialiased seam.
    frame, attached = notch.entrance_geometry(
        (0, 0, 1471, 957), (0, 76, 1471, 847), 32,
        (0, 924, 646.4, 32), (825.6, 924, 645, 32))
    assert attached
    assert all(v == round(v) for v in frame)


@pytest.mark.parametrize("origin", [(-1920, 0), (0, 956), (1470, -200)])
def test_external_display_capsule_stays_within_its_own_visible_frame(origin):
    x, y = origin
    frame, attached = notch.entrance_geometry((x, y, 1920, 1080), (x, y, 1920, 1055))
    assert not attached
    assert x <= frame[0] < frame[0] + frame[2] <= x + 1920
    assert y <= frame[1] < frame[1] + frame[3] < y + 1055


def test_hidden_menu_keeps_entrance_inside_the_notch_gap():
    frame, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 0, 1470, 956), 32,
        (0, 924, 646, 32), (825, 924, 645, 32))
    assert attached
    # Even with the menu bar auto-hidden, the width never spills past the gap
    # into where side menu/status items sit.
    assert frame[0] >= 646 and frame[0] + frame[2] <= 825


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
