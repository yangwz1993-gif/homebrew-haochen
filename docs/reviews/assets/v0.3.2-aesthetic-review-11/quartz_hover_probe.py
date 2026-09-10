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


parser = argparse.ArgumentParser()
parser.add_argument("--pid", type=int, required=True)
parser.add_argument("--case", choices=("A", "B", "C"), required=True)
parser.add_argument("--app-log", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()


def result_window():
    matches = []
    for window in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID):
        if int(window.get("kCGWindowOwnerPID", -1)) != args.pid:
            continue
        bounds = window.get("kCGWindowBounds")
        if not bounds or float(bounds.get("Width", 0)) < 250:
            continue
        matches.append({
            "number": int(window.get("kCGWindowNumber", 0)),
            "owner_pid": int(window.get("kCGWindowOwnerPID", 0)),
            "owner": str(window.get("kCGWindowOwnerName", "")),
            "layer": int(window.get("kCGWindowLayer", 0)),
            "alpha": float(window.get("kCGWindowAlpha", 0)),
            "bounds": {key: float(bounds[key]) for key in ("X", "Y", "Width", "Height")},
        })
    return max(matches, key=lambda row: row["bounds"]["Width"] * row["bounds"]["Height"]) if matches else None


def cursor():
    location = CGEventGetLocation(CGEventCreate(None))
    return [float(location.x), float(location.y)]


def inside(location, bounds):
    x, y = location
    return bounds["X"] <= x < bounds["X"] + bounds["Width"] and bounds["Y"] <= y < bounds["Y"] + bounds["Height"]


events = []


def record(name, window=None, **extra):
    row = {"event": name, "monotonic": time.monotonic(), "mouse": cursor(), "window": window}
    if window:
        row["inside"] = inside(row["mouse"], window["bounds"])
    row.update(extra)
    events.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


outside = (900.0, 100.0)
CGWarpMouseCursorPosition(outside)
with open(args.app_log, "r", encoding="utf-8") as fp:
    baseline = fp.read().count("\n")

deadline = time.monotonic() + 180.0
while time.monotonic() < deadline:
    with open(args.app_log, "r", encoding="utf-8") as fp:
        new_lines = fp.readlines()[baseline:]
    if any("state COMPOSING -> PRESENTING" in line for line in new_lines):
        break
    time.sleep(0.005)
else:
    raise SystemExit("Timed out waiting for PRESENTING state")

deadline = time.monotonic() + 2.0
window = result_window()
while window is None and time.monotonic() < deadline:
    time.sleep(0.005)
    window = result_window()
if window is None:
    raise SystemExit("No result bubble for candidate PID after PRESENTING")

record("appeared", window, case=args.case)

if args.case == "A":
    time.sleep(8.0)
    record("outside_8s", result_window())
    window = result_window()
    bounds = window["bounds"]
    CGWarpMouseCursorPosition((bounds["X"] + bounds["Width"] * 0.5, bounds["Y"] + bounds["Height"] * 0.45))
    record("entered", result_window())
    time.sleep(8.0)
    record("held_8s", result_window())
    CGWarpMouseCursorPosition(outside)
    record("left", result_window())
elif args.case == "B":
    bounds = window["bounds"]
    CGWarpMouseCursorPosition((bounds["X"] + bounds["Width"] * 0.5, bounds["Y"] + bounds["Height"] * 0.45))
    record("entered", result_window())
    time.sleep(22.0)
    record("held_22s", result_window())
    CGWarpMouseCursorPosition(outside)
    record("left", result_window())
else:
    record("outside_no_hover", result_window())

deadline = time.monotonic() + 30.0
while time.monotonic() < deadline:
    if result_window() is None:
        record("gone")
        break
    time.sleep(0.02)
else:
    record("still_present_after_30s", result_window())

with open(args.output, "w", encoding="utf-8") as fp:
    json.dump(events, fp, ensure_ascii=False, indent=2)
