#!/usr/bin/env python3
import argparse
import json
import time

from Quartz import (
    CGEventCreate,
    CGEventGetLocation,
    CGWarpMouseCursorPosition,
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowListOptionOnScreenOnly,
)
from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementCreateApplication


parser = argparse.ArgumentParser()
parser.add_argument("--pid", type=int, required=True)
parser.add_argument("--case", choices=("A", "B"), required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--wait-log")
parser.add_argument("--wait-prompt")
args = parser.parse_args()


def bubble():
    matches = []
    for window in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID):
        if window.get("kCGWindowOwnerPID") != args.pid:
            continue
        bounds = window.get("kCGWindowBounds")
        if not bounds or float(bounds.get("Width", 0)) < 250:
            continue
        matches.append(
            {
                "number": int(window.get("kCGWindowNumber", 0)),
                "owner_pid": int(window.get("kCGWindowOwnerPID", 0)),
                "owner": str(window.get("kCGWindowOwnerName", "")),
                "layer": int(window.get("kCGWindowLayer", 0)),
                "alpha": float(window.get("kCGWindowAlpha", 0)),
                "bounds": {key: float(bounds[key]) for key in ("X", "Y", "Width", "Height")},
            }
        )
    if not matches:
        return None
    return max(matches, key=lambda item: item["bounds"]["Width"] * item["bounds"]["Height"])


def point():
    location = CGEventGetLocation(CGEventCreate(None))
    return [float(location.x), float(location.y)]


def is_inside(location, bounds):
    x, y = location
    return (
        bounds["X"] <= x < bounds["X"] + bounds["Width"]
        and bounds["Y"] <= y < bounds["Y"] + bounds["Height"]
    )


def ax_result_visible():
    root = AXUIElementCreateApplication(args.pid)
    stack = [root]
    seen = set()
    while stack:
        element = stack.pop()
        marker = str(element)
        if marker in seen:
            continue
        seen.add(marker)
        for attribute in ("AXTitle", "AXDescription", "AXValue"):
            error, value = AXUIElementCopyAttributeValue(element, attribute, None)
            if error == 0 and value and "继续提问" in str(value):
                return True
        error, children = AXUIElementCopyAttributeValue(element, "AXChildren", None)
        if error == 0 and children:
            stack.extend(list(children))
    return False


events = []


def record(event, window=None, **extra):
    row = {"event": event, "monotonic": time.monotonic(), "mouse": point(), "window": window}
    if window:
        row["inside"] = is_inside(row["mouse"], window["bounds"])
    row.update(extra)
    events.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


outside = (900.0, 100.0)
CGWarpMouseCursorPosition(outside)

if args.wait_log:
    with open(args.wait_log, "r", encoding="utf-8") as fp:
        baseline = fp.read().count("\n")
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        with open(args.wait_log, "r", encoding="utf-8") as fp:
            lines = fp.readlines()[baseline:]
        matched = False
        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if item.get("event") != "response_done":
                continue
            if args.wait_prompt and args.wait_prompt not in item.get("prompt", ""):
                continue
            matched = True
            break
        if matched:
            break
        time.sleep(0.01)
    else:
        raise SystemExit("Timed out waiting for fixture completion")
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if ax_result_visible():
            break
        time.sleep(0.01)
    else:
        raise SystemExit("Timed out waiting for visible result state")

window = bubble()
if window is None:
    raise SystemExit("No result bubble found for owner PID")

record("appeared", bubble(), case=args.case)

if args.case == "A":
    time.sleep(8.0)
    window = bubble()
    record("outside_8s", window)
    if window is None:
        record("gone_before_enter")
    else:
        bounds = window["bounds"]
        entered_point = (bounds["X"] + bounds["Width"] * 0.5, bounds["Y"] + bounds["Height"] * 0.45)
        CGWarpMouseCursorPosition(entered_point)
        record("entered", bubble())
        time.sleep(8.0)
        record("held_8s", bubble())
        CGWarpMouseCursorPosition(outside)
        record("left", bubble())
else:
    bounds = window["bounds"]
    entered_point = (bounds["X"] + bounds["Width"] * 0.5, bounds["Y"] + bounds["Height"] * 0.45)
    CGWarpMouseCursorPosition(entered_point)
    record("entered", bubble())
    time.sleep(22.0)
    record("held_22s", bubble())
    CGWarpMouseCursorPosition(outside)
    record("left", bubble())

deadline = time.monotonic() + 30.0
while time.monotonic() < deadline:
    current = bubble()
    if current is None:
        record("gone")
        break
    time.sleep(0.05)
else:
    record("still_present_after_30s", bubble())

with open(args.output, "w", encoding="utf-8") as fp:
    json.dump(events, fp, ensure_ascii=False, indent=2)
