"""Window identity only. Never read body text, capture pixels or request access here."""
import hashlib
import os
import time

import AppKit
from ApplicationServices import (
    AXIsProcessTrustedWithOptions,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementSetAttributeValue,
    AXUIElementSetMessagingTimeout,
)
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionAll,
    kCGWindowListOptionOnScreenOnly,
)


def attribute(element, name):
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def web_area(window):
    """Find a page object by metadata only; callers may retain it across focus changes."""
    pending = [(window, 0)]
    deadline = time.monotonic() + 0.5
    for _ in range(300):
        if not pending or time.monotonic() > deadline:
            break
        element, depth = pending.pop(0)
        if attribute(element, "AXRole") == "AXWebArea":
            return element
        if depth < 14:
            pending.extend((child, depth + 1) for child in (attribute(element, "AXChildren") or []))
    return None


def document_id(window):
    """Use the page's identity, not Chromium's potentially stale AXDocument.

    SPA navigation can update the page and window title while AXDocument still
    names the site's previous document. File-backed native windows keep their
    document URL. No body values or address-bar keystrokes are used here.
    """
    document = attribute(window, "AXDocument")
    if document and str(document).startswith("file:"):
        return str(document)
    page = web_area(window)
    if page is not None:
        return str(attribute(page, "AXURL") or "")
    return str(document or "")


def fingerprint(window):
    value = str(attribute(window, "AXTitle") or "") + "\n" + document_id(window)
    return hashlib.sha256(value.encode()).hexdigest()


def window_list(pid, *, all_windows=False):
    windows = CGWindowListCopyWindowInfo(
        (kCGWindowListOptionAll if all_windows else kCGWindowListOptionOnScreenOnly)
        | kCGWindowListExcludeDesktopElements, 0)
    return [w for w in windows or [] if int(w.get("kCGWindowOwnerPID", -1)) == pid
            and int(w.get("kCGWindowLayer", -1)) == 0]


def bounds(window):
    from ApplicationServices import AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType
    try:
        ok_p, pos = AXValueGetValue(attribute(window, "AXPosition"), kAXValueCGPointType, None)
        ok_s, size = AXValueGetValue(attribute(window, "AXSize"), kAXValueCGSizeType, None)
        if ok_p and ok_s:
            return (pos.x, pos.y, size.width, size.height)
    except Exception:
        pass
    return None


def matching_ax_window(pid, window_id):
    """Resolve a fixed CG window to AX, refusing ambiguous matches."""
    cg = next((w for w in window_list(pid, all_windows=True) if int(w["kCGWindowNumber"]) == window_id), None)
    if cg is None:
        return None
    app = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(app, 0.05)
    rect = cg.get("kCGWindowBounds", {})
    expected = tuple(float(rect.get(k, -99999)) for k in ("X", "Y", "Width", "Height"))
    matches = []
    for window in attribute(app, "AXWindows") or []:
        actual = bounds(window)
        if actual and all(abs(a - b) < 2 for a, b in zip(actual, expected, strict=True)):
            matches.append(window)
    return matches[0] if len(matches) == 1 else None


def describe_target(pid):
    windows = window_list(pid)
    # macOS sharing indicators can be layer-0 windows owned by the document app.
    # They are chrome, not the user's reading target.
    windows = [w for w in windows if float(w.get("kCGWindowBounds", {}).get("Width", 0)) >= 120
               and float(w.get("kCGWindowBounds", {}).get("Height", 0)) >= 60]
    if not windows:
        return None
    trusted = AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": False})
    if trusted:
        app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.05)
        # Chromium exposes document metadata only after its accessibility tree is enabled.
        # No body values are queried here, and this never requests system permission.
        for flag in ("AXEnhancedUserInterface", "AXManualAccessibility"):
            if not attribute(app, flag):
                AXUIElementSetAttributeValue(app, flag, True)
        focused = attribute(app, "AXFocusedWindow")
        focused_bounds = bounds(focused) if focused is not None else None
        if focused_bounds:
            matches = [w for w in windows if all(
                abs(float(w.get("kCGWindowBounds", {}).get(k, -99999)) - v) < 2
                for k, v in zip(("X", "Y", "Width", "Height"), focused_bounds, strict=True))]
            if len(matches) == 1:
                windows = matches
    # Otherwise use front-to-back order. Never substitute the largest window.
    window = windows[0]
    target = {"pid": pid, "window_id": int(window["kCGWindowNumber"]),
              "app": str(window.get("kCGWindowOwnerName", "应用")),
              "title": str(window.get("kCGWindowName", "")), "fingerprint": ""}
    if trusted:
        ax = matching_ax_window(pid, target["window_id"])
        if ax is not None:
            target["title"] = str(attribute(ax, "AXTitle") or "")[:180]
            # Capture each metadata value once. A timed-out second URL lookup must
            # not turn an unchanged document into a different fingerprint.
            target["document_uri"] = document_id(ax)
            target["fingerprint"] = hashlib.sha256(
                (target["title"] + "\n" + target["document_uri"]).encode()).hexdigest()
    return target


def current_target(previous_pid=0):
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = int(app.processIdentifier()) if app else 0
    if pid == os.getpid():
        pid = previous_pid
    return describe_target(pid) if pid > 1 and pid != os.getpid() else None
