#!/usr/bin/env python3
"""Exercise only haochen-owned native windows, with synthetic local fixtures.

No Accessibility, screen-recording, credentials, connectors, user files or other
apps are read or changed. Native key events go to our own NSPanel window only.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def main():
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    AK = importlib.import_module("AppKit")
    NativeDashboard = importlib.import_module("haochen_app.dashboard.native_window").NativeDashboard
    scratch = tempfile.TemporaryDirectory(prefix="haochen-notch-native-")
    output = Path(scratch.name)
    (output / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><style>body{padding:50px;background:#edf1e9;'
        'font:22px -apple-system}input{font:inherit;padding:20px}</style>'
        '<h1>haochen 原生刘海测试</h1><input id="draft" value="保留这个草稿">'
        '<script>window.haochenVisibilityChanged=x=>window.lastVisibility=x;</script>', encoding="utf-8")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    window = NativeDashboard(output)
    window.set_settings({"dock": "notch", "motion": "reduced"})
    window.set_activity({"kind": "attention", "count": 2, "label": "等你确认",
                         "accessibleLabel": "2 项待确认，打开 haochen 桌面总览"})
    outcomes = {}
    captures = []
    native_screens = AK.NSScreen.screens()
    outcomes["normal_overview_level"] = window.panel.level() == AK.NSNormalWindowLevel
    outcomes["bounded_floating_entrance"] = (window.handle.panel.level() == AK.NSFloatingWindowLevel
                                            and window.handle.panel.frame().size.width <= 400)
    outcomes["no_all_spaces_overview"] = not bool(
        window.panel.collectionBehavior() & AK.NSWindowCollectionBehaviorCanJoinAllSpaces)

    def capture_notch(open_overview=True):
        button = window.handle.button
        rep = button.bitmapImageRepForCachingDisplayInRect_(button.bounds())
        button.cacheDisplayInRect_toBitmapImageRep_(button.bounds(), rep)
        png = rep.representationUsingType_properties_(AK.NSBitmapImageFileTypePNG, {})
        path = Path(tempfile.gettempdir()) / "haochen-v05b2-notch-status.png"
        if png.writeToFile_atomically_(str(path), True):
            captures.append(str(path))
        outcomes["status_accessibility"] = str(button.accessibilityLabel()) == "2 项待确认，打开 haochen 桌面总览"
        frame = window.handle.panel.frame()
        screen = window._ns_screen()
        if screen.safeAreaInsets().top > 0:
            # Seamless notch: the surface is flush with the screen's top edge and
            # covers the notch gap, so its top (origin.y + height) equals the top
            # of the screen rather than the notch's lower edge.
            outcomes["physical_notch_attachment"] = (
                window.handle.button.attached and
                frame.origin.y + frame.size.height == screen.frame().size.height +
                screen.frame().origin.y)
        else:
            outcomes["physical_notch_attachment"] = not window.handle.button.attached
        outcomes["screen_retained"] = window._ns_screen() in native_screens
        # Send the NSButton action through its native control action mechanism.
        if open_overview:
            window.handle.button.performClick_(None)

    def send_m(modifiers=None):
        factory = getattr(AK.NSEvent, "keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_"
                                     "characters_charactersIgnoringModifiers_isARepeat_keyCode_")
        event = factory(
            AK.NSEventTypeKeyDown, AK.NSMakePoint(50, 50),
            AK.NSEventModifierFlagCommand if modifiers is None else modifiers, 0,
            window.panel.windowNumber(), None, "m", "m", False, 46)
        # NSApplication's actual event path invokes the active NSPanel/WK
        # equivalent; calling owner.hide() would not validate the shortcut.
        AK.NSApplication.sharedApplication().sendEvent_(event)

    def command_collapse():
        outcomes["native_entrance_opens"] = window.expanded and window.panel.isKeyWindow()
        send_m()
        outcomes["native_cmd_m_collapses"] = not window.expanded
        outcomes["same_entrance_returns"] = window.handle.panel.isVisible()
        outcomes["reduced_motion_finishes"] = not window.panel.isVisible()
        window.show()

    def draft_preserved():
        window.webview.evaluateJavaScript_completionHandler_(
            "document.querySelector('#draft').value",
            lambda result, error: outcomes.update(draft_preserved=error is None and result == "保留这个草稿"))
        window.evaluate("document.querySelector('#draft').focus()")

    def ime_guard():
        window.webview.setMarkedText_selectedRange_replacementRange_("你好", AK.NSMakeRange(2, 0), AK.NSMakeRange(0, 0))

        def composed():
            outcomes["native_marked_text_received"] = window._native_composing
            send_m()
            outcomes["cmd_m_keeps_ime_composition"] = window.expanded and window._native_composing
            window.webview.insertText_replacementRange_("你好", AK.NSMakeRange(0, 2))
            outcomes["native_commit_finishes_composition"] = not window._native_composing

        QTimer.singleShot(150, composed)

    other = AK.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        AK.NSMakeRect(200, 200, 380, 220), AK.NSWindowStyleMaskTitled,
        AK.NSBackingStoreBuffered, False)
    other.setReleasedWhenClosed_(False)
    other.setTitle_("haochen 测试窗口 · 层级验证")
    other.setLevel_(AK.NSNormalWindowLevel)

    def normal_front():
        other.makeKeyAndOrderFront_(None)
        # NSApplication.orderedWindows omits utility panels; the public native
        # window-number list includes them. Options=0 means THIS app only, so
        # no other application's window titles or screen contents are read.
        numbers = list(AK.NSWindow.windowNumbersWithOptions_(0) or [])
        ours, front = window.panel.windowNumber(), other.windowNumber()
        outcomes["another_normal_window_can_cover_overview"] = (ours in numbers and front in numbers
            and numbers.index(front) < numbers.index(ours))
        outcomes["inactive_overview_cmd_m_not_stolen"] = not window.collapse_if_active() and window.expanded
        other.orderOut_(None)
        window.show()
        window.set_settings({"dock": "pet", "motion": "reduced"})
        window.hide()
        outcomes["pet_only_has_no_second_entrance"] = not window.handle.panel.isVisible()
        window.set_settings({"dock": "notch", "motion": "reduced"})
        outcomes["notch_mode_returns_single_entrance"] = window.handle.panel.isVisible()

    def finish():
        other.close()
        window.stop()
        print(json.dumps({"checks": outcomes, "captures": captures}, ensure_ascii=False))
        app.exit(0 if outcomes and all(outcomes.values()) else 1)

    window.summon_requested.connect(window.show)
    window.start()
    if "--entrance-only" in sys.argv:
        # Does not activate, open a work surface or send any keyboard event.
        QTimer.singleShot(300, lambda: capture_notch(False))
        QTimer.singleShot(600, finish)
    else:
        QTimer.singleShot(1000, capture_notch)
        QTimer.singleShot(1800, command_collapse)
        QTimer.singleShot(2200, draft_preserved)
        QTimer.singleShot(2700, ime_guard)
        QTimer.singleShot(3200, normal_front)
        QTimer.singleShot(3800, finish)
    QTimer.singleShot(10000, lambda: app.exit(2))
    result = app.exec()
    scratch.cleanup()
    return result


if __name__ == "__main__":
    sys.exit(main())
