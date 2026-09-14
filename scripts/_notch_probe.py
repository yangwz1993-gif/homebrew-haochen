#!/usr/bin/env python3
"""Dev-only: show ONLY the notch entrance pill (non-activating) and screenshot
the notch region, so the seam can be inspected before/after a visual change.

Run with app/.venv/bin/python scripts/_notch_probe.py [out.png]. Does not launch
the engine, onboarding, model, or the focus-stealing overview. Screenshotting the
composited region needs Screen Recording permission, which the user grants.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/notch.png"


def main():
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication

    app_kit = importlib.import_module("AppKit")
    notch = importlib.import_module("haochen_app.dashboard.notch")

    app = QApplication.instance() or QApplication([])
    screens = app_kit.NSScreen.screens()
    screen = next((s for s in screens if s.safeAreaInsets().top > 0), app_kit.NSScreen.mainScreen())
    f, vf = screen.frame(), screen.visibleFrame()
    frame = (f.origin.x, f.origin.y, f.size.width, f.size.height)
    visible = (vf.origin.x, vf.origin.y, vf.size.width, vf.size.height)
    aux_l = screen.auxiliaryTopLeftArea()
    aux_r = screen.auxiliaryTopRightArea()
    left = (aux_l.origin.x, aux_l.origin.y, aux_l.size.width, aux_l.size.height)
    right = (aux_r.origin.x, aux_r.origin.y, aux_r.size.width, aux_r.size.height)
    geom, attached = notch.entrance_geometry(frame, visible, screen.safeAreaInsets().top, left, right)
    print("attached:", attached, "geom:", geom)

    class Owner(QObject):
        summon_requested = pyqtSignal()

    entrance = notch.NativeEntrance(Owner())
    entrance.place(geom, attached)
    entrance.set_activity({"kind": "running", "label": "正在处理", "count": 3})
    entrance.show()

    top_y = f.size.height - (geom[1] + geom[3])
    region = f"{int(geom[0]) - 40},{max(0, int(top_y) - 8)},{int(geom[2]) + 80},{int(geom[3]) + 24}"

    def capture():
        subprocess.run(["screencapture", "-x", "-R", region, OUT], check=False)
        print("captured", OUT, "region", region)
        entrance.close()
        app.quit()

    QTimer.singleShot(700, capture)
    app.exec()


if __name__ == "__main__":
    main()
