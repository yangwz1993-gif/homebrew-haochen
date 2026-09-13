"""Explicit source/frozen smoke check against the real dashboard, not fake replies.

Only a new, caller-named temporary output directory may be used. The real
AppShell uses its in-memory credential backend, but its engine is never started.
The production WK UI talks to its production controller/service; the only input
fixture is a clearly labelled file created inside this diagnostic directory.
No calendar authorization, Chrome installation, other-app screen capture, or
remote model request occurs. This is a technical smoke check, not user acceptance.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path


def prepare_output(raw: str) -> Path:
    """Create, never reuse, a private directory below macOS temp or /tmp."""
    requested = Path(raw)
    if not requested.is_absolute() or ".." in requested.parts:
        raise ValueError("诊断输出必须是临时目录下的绝对路径")
    parent = requested.parent.resolve(strict=True)
    roots = [Path("/tmp").resolve()]
    system_temp = Path(tempfile.gettempdir()).resolve()
    if str(system_temp).startswith("/private/var/folders/") and system_temp.name == "T":
        roots.append(system_temp)
    if not any(parent == root or parent.is_relative_to(root) for root in roots):
        raise ValueError("诊断输出仅允许 /tmp 或当前 macOS 临时目录")
    if parent.stat().st_uid not in (os.getuid(), 0):
        raise ValueError("临时目录不属于当前用户")
    output = parent / requested.name
    output.mkdir(mode=0o700, exist_ok=False)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-check", action="store_true", required=True)
    parser.add_argument("--dashboard-check-output", required=True)
    args = parser.parse_args(argv)
    try:
        output = prepare_output(args.dashboard_check_output)
    except (OSError, ValueError) as error:
        if sys.stderr is not None:
            print(str(error), file=sys.stderr)
        return 2

    # No imports which can resolve user configuration occur before isolation.
    home = output / "isolated-home"
    home.mkdir(mode=0o700)
    os.environ["HAOCHEN_HOME"] = str(home)
    os.environ["HAOCHEN_MOCK"] = "1"
    os.environ["HAOCHEN_SKIP_ONBOARDING"] = "1"
    report = {
        "passed": False,
        "status": "running",
        "frozen": bool(getattr(sys, "frozen", False)),
        "scope": "真实生产 WK/Controller/Service；仅隔离文件证据；不是外部连接或用户验收",
        "checks": [],
        "screenshots": [],
        "startedAt": datetime.now().astimezone().isoformat(),
    }
    completed = threading.Event()
    lock = threading.RLock()

    def save():
        # All destinations belong to the freshly-created diagnostic directory.
        temporary = output / "results.json.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(output / "results.json")

    def check(name, ok, detail=""):
        with lock:
            report["checks"].append({"check": name, "passed": bool(ok), "detail": str(detail)[:1000]})
            save()
        if not ok:
            raise ValueError("诊断检查未通过：" + name)

    def watchdog():
        if not completed.wait(55):
            with lock:
                report.update(status="timeout", passed=False)
                report["checks"].append({"check": "hard-timeout", "passed": False})
                save()
            # A wedged native callback must not leave an invisible test process.
            os._exit(2)

    save()
    threading.Thread(target=watchdog, name="haochen-dashboard-check-timeout", daemon=True).start()
    app, shell, controller = None, None, None
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication

        from .. import paths
        from ..app_shell import AppShell
        from ..keychain import MemoryCredentialStore
        from .controller import DashboardController

        # Loading the public framework module checks frozen packaging only; do
        # not construct EKEventStore or call authorization/collection methods.
        importlib.import_module("EventKit")
        AK = importlib.import_module("AppKit")
        assets = paths.dashboard_assets()
        check(
            "production-assets",
            all(
                (assets / name).is_file()
                for name in (
                    "index.html",
                    "dashboard.js",
                    "dashboard.css",
                    "icons.js",
                )
            ),
            str(assets),
        )
        app = QApplication(sys.argv[:1])
        app.setApplicationName("haochen · 隔离诊断")
        app.setQuitOnLastWindowClosed(False)
        shell = AppShell(mock=True, home=home)
        check("memory-credentials", isinstance(shell.store.keychain, MemoryCredentialStore))
        controller = DashboardController(shell)
        shell.dashboard = controller
        for name in ("otty", "browser", "calendar", "wechat"):
            controller.service.enable(name, False)
        controller.service.store.settings_update({"motion": "reduced", "dock": "side", "aiDaily": False})
        fixture = output / "SELF-CHECK-fixture.md"
        body = (
            "# SELF-CHECK · 隔离测试资料\n这是诊断自身创建的 UTF-8 文件，不是用户资料。\n阶段：已完成文件读取验证。\n"
        )
        fixture.write_text(body, encoding="utf-8")
        fixture.chmod(0o600)
        window = controller.window
        # Keep the real controller, but make this explicitly labelled diagnostic
        # incapable of opening setup/authorization if the user happens to click
        # a production button while it is running. Never forward other paths or
        # an AI-enabled track into this isolated check.
        window.message.disconnect(controller.handle)

        def diagnostic_message(message):
            action = message.get("action")
            identifier = message.get("id", "")
            payload = message.get("payload", {})
            allowed = action in {"ready", "reportGet"}
            if action == "trackCreate" and identifier == "selfcheck-track":
                allowed = payload.get("aiEnabled") is False and all(
                    source.get("type") == "file"
                    and source.get("locator")
                    in {
                        item["id"]
                        for item in controller.service.store.snapshot()["files"]
                        if item["path"] == str(fixture)
                    }
                    for source in payload.get("sources", [])
                )
            if allowed:
                controller.handle(message)
            else:
                window.send({"id": identifier, "ok": False, "error": "隔离诊断不执行外部操作或授权"})

        window.message.connect(diagnostic_message)
        window.files_dropped.disconnect(controller.add_files)
        window.files_dropped.connect(
            lambda selections: controller.add_files([value for value in selections if value == str(fixture)])
        )
        started = False
        finished = False

        def finish(ok, detail=""):
            nonlocal finished
            if finished:
                return
            finished = True
            with lock:
                report.update(
                    passed=bool(ok),
                    status="passed" if ok else "failed",
                    finishedAt=datetime.now().astimezone().isoformat(),
                )
                if detail:
                    report["error"] = str(detail)[:1000]
                save()
            controller.stop()
            shell.stop()
            shell.pet.pet.close()
            shell.pet.bubble.hide()
            shell.chat.hide()
            shell.settings.hide()
            completed.set()
            app.exit(0 if ok else 1)

        def guard(function):
            def wrapped(*values):
                if finished:
                    return
                try:
                    return function(*values)
                except Exception as error:
                    finish(False, type(error).__name__ + ": " + str(error))

            return wrapped

        def javascript(source, callback):
            def done(value, error):
                if error is not None:
                    finish(False, "生产 JavaScript 求值失败")
                    return
                callback(value)

            window.webview.evaluateJavaScript_completionHandler_(source, guard(done))

        def await_js(expression, callback, timeout=8):
            deadline = time.monotonic() + timeout

            def poll():
                def inspected(value):
                    if value:
                        callback()
                    elif time.monotonic() >= deadline:
                        finish(False, "等待真实桥接响应超时")
                    else:
                        QTimer.singleShot(80, guard(poll))

                javascript("Boolean(" + expression + ")", inspected)

            poll()

        def send(action, payload, identifier):
            message = {"v": 1, "id": identifier, "action": action, "payload": payload}
            window.evaluate("window.webkit.messageHandlers.haochen.postMessage(" + json.dumps(message) + ")")

        def capture():
            def captured(image, error):
                check("own-wk-snapshot", image is not None and error is None)
                bitmap = AK.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                data = bitmap.representationUsingType_properties_(AK.NSBitmapImageFileTypePNG, {})
                screenshot = output / "dashboard-check.png"
                check("snapshot-written", bool(data.writeToFile_atomically_(str(screenshot), True)))
                screenshot.chmod(0o600)
                report["screenshots"].append(str(screenshot))
                check("engine-never-started", shell.supervisor.client._proc is None)
                check("no-calendar-access", controller.service.adapters["calendar"]._store is None)
                check("no-browser-install", not (home / "dashboard/browser-bridge/host-config.json").exists())
                check(
                    "isolated-real-service",
                    controller.service.store.path.is_relative_to(home)
                    and controller.service.summarizer.config.home == home,
                )
                finish(True)

            window.webview.takeSnapshotWithConfiguration_completionHandler_(None, guard(captured))

        def reported():
            state = controller.service.store.snapshot()
            current = next(report for report in state["reports"] if report["date"] == date)
            entries = [item for section in current["sections"] for item in section["items"]]
            check(
                "real-daily-report",
                any(
                    "SELF-CHECK" in item["text"] and any(e.get("text") == body for e in item.get("evidence", []))
                    for item in entries
                ),
            )
            window.evaluate("document.querySelector('[data-action=report]').click()")
            await_js("document.querySelector('#float-body')?.textContent.includes('SELF-CHECK')", capture)

        def tracked():
            state = controller.service.store.snapshot()
            track = next(track for track in state["tracks"] if track["title"] == "SELF-CHECK · 文件追踪")
            evidence = track["evidence"]
            check(
                "real-file-evidence",
                track["status"] == "ready"
                and len(evidence) == 1
                and evidence[0].get("text") == body
                and bool(track.get("digest")),
            )
            check("ai-disabled", track.get("aiSummary") is False)
            send("reportGet", {"date": date}, "selfcheck-report")
            await_js("window.__haochenCheck.replies['selfcheck-report']?.ok === true", reported)

        def create_track():
            check("real-js-bridge", True)
            item = next(item for item in controller.service.store.snapshot()["files"] if item["path"] == str(fixture))
            check("native-file-drop-boundary", True)
            payload = {
                "title": "SELF-CHECK · 文件追踪",
                "goal": "读取本诊断明确添加的资料",
                "frequency": "manual",
                "aiEnabled": False,
                "sources": [{"type": "file", "locator": item["id"], "label": "SELF-CHECK · 临时文件"}],
            }
            send("trackCreate", payload, "selfcheck-track")
            await_js(
                "window.__haochenCheck.state?.tracks.some(t => t.title === 'SELF-CHECK · 文件追踪'"
                " && t.status === 'ready') && window.__haochenCheck.replies['selfcheck-track']?.ok === true",
                tracked,
            )

        def loaded():
            nonlocal started
            if started:
                return
            started = True
            check("real-wk-loaded", window.loaded)
            script = """(() => {
                if (typeof window.haochenReceive !== 'function') return false;
                const receive = window.haochenReceive;
                window.__haochenCheck = {replies: {}, state: null};
                window.haochenReceive = message => {
                    if (message.id) window.__haochenCheck.replies[message.id] = message;
                    if (message.state) window.__haochenCheck.state = message.state;
                    return receive(message);
                };
                return true;
            })()"""

            def installed(value):
                check("production-js-ready", value is True)
                # Exercise the same boundary as an OS file drop without opening
                # a picker or simulating a drop from an unrelated application.
                window.files_dropped.emit([str(fixture)])
                send("ready", {}, "selfcheck-ready")
                await_js(
                    "window.__haochenCheck.replies['selfcheck-ready']?.ok === true"
                    " && window.__haochenCheck.state?.files.length === 1",
                    create_track,
                )

            javascript(script, installed)

        date = datetime.now().astimezone().date().isoformat()
        window.ready.connect(guard(loaded))
        controller.start()
        controller.show()
        QTimer.singleShot(45000, lambda: finish(False, "原生诊断超时"))
        result = app.exec()
        if not completed.is_set():
            finish(False, "诊断提前退出")
        return result
    except Exception as error:
        with lock:
            report.update(passed=False, status="failed", error=type(error).__name__ + ": " + str(error)[:1000])
            save()
        if controller is not None:
            controller.stop()
        if shell is not None:
            shell.stop()
        completed.set()
        return 1
