#!/usr/bin/env python3
"""Own-window WKWebView smoke test, isolated from user config/permissions.

Run with app/.venv/bin/python scripts/verify_dashboard_native.py. The --fixture
option exercises the native boundary before production assets are available.
It neither starts the engine nor reads another app's screen or messages.
"""

from __future__ import annotations

import argparse
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

    NSMakeRange = importlib.import_module("Foundation").NSMakeRange
    NativeDashboard = importlib.import_module("haochen_app.dashboard.native_window").NativeDashboard

    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--output", default="/tmp/haochen-v05-native.png")
    arguments = parser.parse_args()
    scratch = tempfile.TemporaryDirectory(prefix="haochen-native-test-")
    assets = ROOT / "app/assets/dashboard"
    if arguments.fixture:
        assets = Path(scratch.name)
        (assets / "index.html").write_text(
            '<!doctype html><meta charset="utf-8"><style>body{background:#f2f5ef;padding:60px;'
            "font:22px -apple-system;color:#334638}input{font:inherit;padding:20px;border-radius:20px}</style>"
            '<h1>haochen · 原生窗口验证</h1><input id="name" placeholder="中文输入">'
            '<p id="result">正在验证本机消息桥</p><script>window.haochenReceive=x=>'
            'document.querySelector("#result").textContent=JSON.stringify(x);'
            'window.webkit.messageHandlers.haochen.postMessage({v:1,id:"fixture",action:"ready",payload:{}})'
            "</script>",
            encoding="utf-8",
        )
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    window = NativeDashboard(assets)
    outcomes = {
        "loaded": False,
        "bridge": False,
        "snapshot": False,
        "reopened": False,
        "ime": False,
        "navigation_denied": False,
        "document_preserved": False,
        "stable_web_layout": False,
    }
    content_sizes = []

    def message(data):
        if data.get("action") == "ready":
            outcomes["bridge"] = True
            window.send({"id": data.get("id"), "ok": True, "message": "本机消息桥已连接"})

    def loaded():
        outcomes["loaded"] = True
        window.send(
            {
                "state": {
                    "events": [],
                    "tracks": [],
                    "files": [],
                    "connectors": [],
                    "reports": [],
                    "calendar": [],
                    "settings": {"palette": "sage"},
                    "updatedAt": "2026-09-13T12:00:00+08:00",
                }
            }
        )

    def screenshot():
        AK = importlib.import_module("AppKit")

        def captured(image, error):
            if image is not None and error is None:
                bitmap = AK.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                data = bitmap.representationUsingType_properties_(AK.NSBitmapImageFileTypePNG, {})
                data.writeToFile_atomically_(arguments.output, True)
                outcomes["snapshot"] = True

        window.webview.takeSnapshotWithConfiguration_completionHandler_(None, captured)

    def ime():
        selector = "#name" if arguments.fixture else 'input[name="title"]'
        if not arguments.fixture:
            window.evaluate("document.querySelector('[data-action=\"track-new\"]').click()")

        # Exercise NSTextInputClient on a real focused production/fixture input;
        # this is no longer marked successful without testing production IME.
        def insert():
            window.evaluate("document.querySelector(" + json.dumps(selector) + ").focus()")

            def commit():
                window.webview.setMarkedText_selectedRange_replacementRange_(
                    "你好", NSMakeRange(2, 0), NSMakeRange(0, 0)
                )
                window.webview.insertText_replacementRange_("你好", NSMakeRange(0, 2))
                window.webview.evaluateJavaScript_completionHandler_(
                    "document.querySelector(" + json.dumps(selector) + ").value",
                    lambda value, _error: outcomes.update(ime=value == "你好"),
                )

            QTimer.singleShot(100, commit)

        QTimer.singleShot(250, insert)

    def forbidden_navigation():
        # A data URL makes no network request and never touches another app/file.
        # The real WebKit navigation callback must cancel it and remain usable.
        window.evaluate('window.location.href="data:text/html,<h1>must-not-load</h1>"')
        QTimer.singleShot(
            200,
            lambda: window.webview.evaluateJavaScript_completionHandler_(
                "window.location.href",
                lambda value, error: outcomes.update(
                    document_preserved=error is None and value == window.document.as_uri()
                ),
            ),
        )

    def reopen():
        window.show()
        outcomes["reopened"] = window.expanded

    def finish():
        outcomes["stable_web_layout"] = len(content_sizes) == 4 and len(set(content_sizes)) == 1
        window.stop()
        print(json.dumps(outcomes))
        app.exit(0 if all(outcomes.values()) else 1)

    window.message.connect(message)
    window.ready.connect(loaded)
    window.navigation_denied.connect(lambda: outcomes.update(navigation_denied=True))
    window.start()
    window.show()

    def sample_layout():
        frame = window.webview.frame()
        content_sizes.append((round(frame.size.width), round(frame.size.height)))

    for elapsed in (80, 180, 280, 390):
        QTimer.singleShot(elapsed, sample_layout)
    QTimer.singleShot(1800, ime)
    QTimer.singleShot(2600, screenshot)
    QTimer.singleShot(3000, forbidden_navigation)
    QTimer.singleShot(3500, window.hide)
    QTimer.singleShot(4200, reopen)
    QTimer.singleShot(5500, finish)
    QTimer.singleShot(12000, lambda: app.exit(2))
    result = app.exec()
    scratch.cleanup()
    return result


if __name__ == "__main__":
    sys.exit(main())
