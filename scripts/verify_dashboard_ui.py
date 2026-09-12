#!/usr/bin/env python3
"""Mechanical WKWebView UI acceptance with explicit, isolated QA fixtures.

Loads the production dashboard into NativeDashboard, but does not construct an
AppShell, engine, connector, keychain, or reader. All bridge replies are in-memory
fixtures. Screenshots capture only this WKWebView through WebKit's snapshot API.
"""

from __future__ import annotations

import copy
import importlib
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def fixture():
    date = datetime.now().astimezone().date().isoformat()
    stamp = datetime.now().astimezone().isoformat()
    evidence = [
        {
            "label": "QA fixture · 来源快照",
            "text": "仅用于机械测试的长内容。" * 160,
            "capturedAt": stamp,
            "coverage": "隔离测试数据，不是用户消息",
        }
    ]
    track = {
        "id": "qa-track",
        "title": "QA fixture · 既有追踪事项",
        "goal": "验证跨渠道追踪表单和草稿恢复。",
        "frequency": "hourly",
        "aiEnabled": False,
        "paused": False,
        "completed": False,
        "status": "partial",
        "conclusion": "这是隔离测试结论，不代表真实设备状态。",
        "lastCheckedAt": stamp,
        "updatedAt": stamp,
        "incomplete": True,
        "sources": [
            {
                "id": "qa-source",
                "type": "file",
                "label": "QA fixture · 文件入口",
                "locator": "/tmp/haochen-ui-qa/feedback.md",
            }
        ],
        "evidence": evidence,
    }
    events = [
        {
            "id": "qa-event",
            "source": "Otty",
            "title": "QA fixture · 很长的应用动态标题 " + "边界内容" * 36,
            "summary": "测试真实布局，不连接设备、不读取任何用户内容。",
            "status": "processing",
            "occurredAt": stamp,
            "evidence": evidence,
        }
    ]
    events.extend(
        {
            "id": f"qa-event-{i}",
            "source": "Chrome",
            "title": f"QA fixture · 动态 {i}",
            "summary": "隔离样本",
            "status": "awaiting",
            "occurredAt": stamp,
            "evidence": [],
        }
        for i in range(1, 5)
    )
    report = {
        "date": date,
        "title": "QA fixture · 日报",
        "summary": "以下全部是隔离测试样本。",
        "generatedAt": stamp,
        "incomplete": True,
        "coverage": "QA fixture；没有读取真实消息或模型结果",
        "sections": [
            {
                "title": "QA fixture · 关键变化",
                "items": [
                    {"text": "长日报内容，用于检查滚动与来源展开。" * 24, "evidence": evidence, "trackId": "qa-track"},
                    {"text": "QA fixture · 另一条记录。" * 60, "evidence": []},
                ],
            }
        ],
    }
    return {
        "events": events,
        "tracks": [track],
        "files": [{"id": "qa-file", "name": "QA fixture · feedback.md", "path": "/tmp/haochen-ui-qa/feedback.md"}],
        "connectors": [
            {
                "id": "otty",
                "name": "Otty",
                "status": "unavailable",
                "enabled": True,
                "summary": "QA fixture：只验证连接卡，不运行 Otty CLI。",
            },
            {
                "id": "browser",
                "name": "Chrome",
                "status": "disabled",
                "enabled": False,
                "summary": "QA fixture：不会安装扩展或触发站点权限。",
            },
            {
                "id": "calendar",
                "name": "日历",
                "status": "connected",
                "enabled": True,
                "summary": "QA fixture：模拟连接，不访问 EventKit。",
            },
            {"id": "wechat", "name": "微信", "status": "limited", "enabled": False, "summary": "本版本能力受限。"},
        ],
        "calendar": [
            {
                "id": "qa-calendar",
                "title": "QA fixture · 日程",
                "startAt": stamp,
                "endAt": "2099-01-01T12:00:00+08:00",
                "location": "仅布局测试",
            }
        ],
        "reports": [report],
        "settings": {"palette": "sage", "dock": "side", "motion": "system"},
        "updatedAt": stamp,
    }


def main():
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    # These modules expose dynamic Objective-C symbols, and the app module is
    # intentionally resolved only after the isolated test adjusts sys.path.
    AK = importlib.import_module("AppKit")
    NSMakeRange = importlib.import_module("Foundation").NSMakeRange
    NativeDashboard = importlib.import_module("haochen_app.dashboard.native_window").NativeDashboard

    output = Path(tempfile.mkdtemp(prefix="haochen-v05-ui-"))
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    window = NativeDashboard(ROOT / "app/assets/dashboard")
    window.set_settings({"dock": "side", "motion": "reduced"})
    state = fixture()
    actions = []
    results = []
    queue = []
    started = False
    stopped = False

    def message(data):
        action, payload = data.get("action"), data.get("payload", {})
        actions.append(copy.deepcopy(data))
        result = None
        if action == "trackCreate" and payload.get("title") == "QA fixture · 预期保存失败":
            window.send({"id": data.get("id"), "ok": False, "error": "QA fixture · 测试保存错误"})
            return
        if action == "trackCreate":
            result = {
                **payload,
                "id": "qa-created",
                "status": "pending",
                "conclusion": "QA fixture · 已保存",
                "paused": False,
                "completed": False,
                "evidence": [],
            }
            state["tracks"].append(result)
        elif action == "trackUpdate":
            item = next(t for t in state["tracks"] if t["id"] == payload["id"])
            item.update(payload)
        elif action == "trackDelete":
            state["tracks"] = [t for t in state["tracks"] if t["id"] != payload["id"]]
        elif action == "settingsUpdate":
            state["settings"].update(payload)
        elif action == "calendarList":
            QTimer.singleShot(
                20,
                lambda: window.send(
                    {
                        "calendars": [
                            {"id": "qa-cal-1", "title": "QA fixture · 工作", "account": "隔离账号", "selected": True},
                            {"id": "qa-cal-2", "title": "QA fixture · 个人", "account": "隔离账号", "selected": False},
                        ],
                        "ok": True,
                    }
                ),
            )
        window.send({"id": data.get("id"), "ok": True, "result": result, "state": copy.deepcopy(state)})

    def finish(ok):
        nonlocal stopped
        if stopped:
            return
        stopped = True
        window.stop()
        report = {
            "passed": ok,
            "checks": results,
            "screenshots": str(output),
            "bridgeActions": [item["action"] for item in actions],
        }
        (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        app.exit(0 if ok else 1)

    def failure(label, detail):
        results.append({"check": label, "passed": False, "detail": str(detail)[:1000]})
        print(f"FAIL {label}: {detail}", flush=True)
        snapshot("failure", lambda: finish(False))

    def js(source, done):
        script = (
            "JSON.stringify((()=>{const q=s=>document.querySelector(s);"
            "const qa=s=>[...document.querySelectorAll(s)];" + source + "})())"
        )

        def callback(value, error):
            if error is not None:
                failure("JavaScript evaluation", error)
                return
            try:
                parsed = json.loads(str(value)) if value is not None else None
            except (ValueError, TypeError):
                parsed = value
            done(parsed)

        window.webview.evaluateJavaScript_completionHandler_(script, callback)

    def next_step():
        if stopped:
            return
        if not queue:
            finish(True)
            return
        step = queue.pop(0)
        step()

    def check(label, source, predicate=lambda value: value is True, timeout=5):
        def run():
            began = time.monotonic()

            def attempt():
                def evaluated(value):
                    if stopped:
                        return
                    if predicate(value):
                        results.append({"check": label, "passed": True})
                        print(f"PASS {label}", flush=True)
                        QTimer.singleShot(20, next_step)
                    elif time.monotonic() - began > timeout:
                        failure(label, value)
                    else:
                        QTimer.singleShot(60, attempt)

                js(source, evaluated)

            attempt()

        queue.append(run)

    def act(source, delay=50):
        queue.append(lambda: js(source, lambda _: QTimer.singleShot(delay, next_step)))

    def native(work, delay=50):
        def run():
            work()
            QTimer.singleShot(delay, next_step)

        queue.append(run)

    def snapshot(name, done):
        def captured(image, error):
            if image is None or error is not None:
                print(f"Snapshot failed: {name}: {error}", flush=True)
            else:
                bitmap = AK.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                data = bitmap.representationUsingType_properties_(AK.NSBitmapImageFileTypePNG, {})
                data.writeToFile_atomically_(str(output / f"{name}.png"), True)
            done()

        window.webview.takeSnapshotWithConfiguration_completionHandler_(None, captured)

    def shot(name):
        check(
            f"{name} motion settled before screenshot",
            "return q('#float-layer').hidden || !q('#float-window').getAnimations().length;",
        )
        queue.append(lambda: snapshot(name, next_step))

    def resize(width, height):
        # Native shell keeps text at its final size while its outer panel morphs.
        # A real display resize updates BOTH the final WK viewport and panel.
        frame = (150, 120, width, height)
        window._size_webview(frame)
        window._set_frame(frame)

    def click(selector):
        act(f"q({json.dumps(selector)}).click(); return true;")

    def native_mouse(selector, down):
        def run():
            def located(point):
                x, y = point
                if not window.webview.isFlipped():
                    y = window.webview.bounds().size.height - y
                location = window.webview.convertPoint_toView_(AK.NSMakePoint(x, y), None)
                make_mouse_event = getattr(
                    AK.NSEvent,
                    "mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_"
                    "eventNumber_clickCount_pressure_",
                )
                event = make_mouse_event(
                    AK.NSEventTypeLeftMouseDown if down else AK.NSEventTypeLeftMouseUp,
                    location,
                    0,
                    time.monotonic(),
                    window.panel.windowNumber(),
                    None,
                    1,
                    1,
                    1.0 if down else 0.0,
                )
                window.panel.sendEvent_(event)
                QTimer.singleShot(120, next_step)

            js(
                f"const r=q({json.dumps(selector)}).getBoundingClientRect();"
                "return [r.left+r.width/2,r.top+r.height/2];",
                located,
            )

        queue.append(run)

    def escape():
        act("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); return true;", 450)

    def loaded():
        nonlocal started
        if started:
            return
        started = True
        window.send({"state": copy.deepcopy(state)})
        window.show()
        QTimer.singleShot(500, next_step)

    native(lambda: resize(860, 680), 150)
    check("wide viewport is actually 860 points", "return innerWidth===860;")
    check(
        "production assets render real DTO fixtures",
        "return qa('.event-card').length===5 && qa('.track-card').length===1;",
    )
    check(
        "no horizontal overflow on long event title",
        "return document.documentElement.scrollWidth<=innerWidth && "
        "q('.event-title').scrollWidth>q('.event-title').clientWidth;",
    )
    act(
        "window.__qaCards=qa('.event-card,.track-card,.calendar-item,.file-open');"
        "q('[data-id=qa-event]').focus();return true;"
    )

    def push_checked_at():
        state["updatedAt"] = datetime.now().astimezone().isoformat()
        state["connectors"][0]["checkedAt"] = state["updatedAt"]
        window.send({"state": copy.deepcopy(state)})

    native(push_checked_at)
    check(
        "collector timestamp push preserves identical card DOM and event focus",
        "return __qaCards.every((node,i)=>node===qa('.event-card,.track-card,.calendar-item,.file-open')[i]) "
        "&& document.activeElement.dataset.focuskey==='event-qa-event';",
    )
    act("q('[data-id=qa-track]').focus();return true;")
    native(push_checked_at)
    check(
        "collector timestamp push preserves track keyboard focus",
        "return document.activeElement===__qaCards.find(node=>node.dataset.id==='qa-track');",
    )
    act("q('[data-id=qa-event]').focus();return true;")

    def push_status(status):
        state["events"][0]["status"] = status
        window.send({"state": copy.deepcopy(state)})

    native(lambda: push_status("changed"))
    check(
        "visible status update retains focus without rebuilding unrelated cards or opening a dialog",
        "return document.activeElement.dataset.focuskey==='event-qa-event' && "
        "q('[data-id=qa-event]').textContent.includes('有变化') && q('#float-layer').hidden && "
        "q('[data-id=qa-track]')===__qaCards.find(node=>node.dataset.id==='qa-track');",
    )
    act(
        "window.__qaPressed=q('[data-id=qa-event]');window.__qaNativeDown=false;"
        "__qaPressed.addEventListener('pointerdown',event=>{window.__qaNativeDown=event.isTrusted;},{once:true});"
        "return true;"
    )
    native_mouse('[data-action="event"][data-id="qa-event"]', True)
    check("native mouse down reaches actual WebKit card", "return window.__qaNativeDown;")
    native(lambda: push_status("awaiting"))
    check(
        "collector completion during mouse press keeps the original hit target",
        "return q('[data-id=qa-event]')===__qaPressed && __qaPressed.isConnected;",
    )
    native_mouse('[data-action="event"][data-id="qa-event"]', False)
    check(
        "native mouse release still opens the latest event after a mid-click update",
        "return !q('#float-layer').hidden && q('#float-body').textContent.includes('等待确认');",
    )
    escape()
    act("q('[data-id=qa-event-4]').focus();return true;")
    removed_event = {}

    def remove_focused_event():
        removed_event.update(state["events"].pop())
        window.send({"state": copy.deepcopy(state)})

    native(remove_focused_event)
    check(
        "removed focused card falls back to its neighbor without automatically opening anything",
        "return q('#float-layer').hidden && !q('[data-id=qa-event-4]') && "
        "document.activeElement.dataset.focuskey==='event-qa-event-3';",
    )

    def restore_events():
        state["events"].append(removed_event)
        state["events"][0]["status"] = "processing"
        window.send({"state": copy.deepcopy(state)})

    native(restore_events)
    shot("01-overview-sage")
    click('[data-action="event"][data-id="qa-event"]')
    check(
        "event opens unified liquid dialog",
        "return !q('#float-layer').hidden && q('#float-title').textContent.includes('应用动态') && "
        "q('#overview').inert;",
    )
    check(
        "liquid animation settles within bounded viewport",
        "const w=q('#float-window'),r=w.getBoundingClientRect();return !w.getAnimations().length && "
        "r.left>=0 && r.right<=innerWidth+1 && r.bottom<=innerHeight+1;",
    )
    shot("02-event-liquid-window")
    escape()
    check(
        "Esc closes event and restores card focus",
        "return q('#float-layer').hidden && document.activeElement.dataset.focuskey==='event-qa-event';",
    )
    click('[data-action="track-new"]')
    act("q('[name=title]').focus(); return true;")

    def ime():
        window.webview.setMarkedText_selectedRange_replacementRange_("你好", NSMakeRange(2, 0), NSMakeRange(0, 0))
        window.webview.insertText_replacementRange_("你好", NSMakeRange(0, 2))

    native(ime, 200)
    check("native NSTextInputClient commits Chinese text", "return q('[name=title]').value==='你好';")
    act(
        "const f=q('#track-form'); f.elements.title.value='QA fixture · 新事项'; "
        "f.elements.goal.value='验证多渠道保存与输入状态'; f.elements.frequency.value='quarter'; "
        "f.elements.aiEnabled.checked=true; f.dispatchEvent(new Event('input',{bubbles:true})); const "
        "t=f.elements['source-type-0'];t.value='file';t.dispatchEvent(new "
        "Event('change',{bubbles:true}));return true;"
    )
    act(
        "const f=q('#track-form');f.elements['source-locator-0'].value='/tmp/haochen-ui-qa/feedback.m"
        "d';f.elements['source-locator-0'].dispatchEvent(new Event('change',{bubbles:true}));return "
        "true;"
    )
    click('[data-action="source-add"]')
    check(
        "source add preserves first selected file and title",
        "const f=q('#track-form');return f.querySelectorAll('[data-source-index]').length===2 && "
        "f.elements['source-locator-0'].value==='/tmp/haochen-ui-qa/feedback.md' && "
        "f.elements.title.value==='QA fixture · 新事项';",
    )
    act(
        "const f=q('#track-form');f.elements['source-locator-1'].value='https://example.com/qa';f.ele"
        "ments['source-label-1'].value='QA fixture · 网页';f.dispatchEvent(new "
        "Event('input',{bubbles:true}));return true;"
    )

    def push_while_editing():
        state["events"].append(
            {"id": "qa-live-added", "source": "Chrome", "title": "QA fixture · 编辑时新到的动态", "status": "idle"}
        )
        window.send({"state": copy.deepcopy(state)})

    native(push_while_editing)
    check(
        "live catalog update does not reset form inputs",
        "const f=q('#track-form');return f.elements.title.value==='QA fixture · 新事项' && "
        "f.elements.goal.value==='验证多渠道保存与输入状态' && "
        "f.elements['source-locator-1'].value==='https://example.com/qa' && "
        "f.elements.aiEnabled.checked;",
    )
    shot("03-track-editor")
    act("q('#track-form').requestSubmit();return true;", 550)
    check(
        "successful create closes only its editor",
        "return q('#float-layer').hidden && [...qa('.track-title')].some(e=>e.textContent==='QA fixture · 新事项');",
    )

    def verify_payload():
        item = next(item for item in reversed(actions) if item["action"] == "trackCreate")["payload"]
        passed = (
            item.get("frequency") == "quarter"
            and item.get("aiEnabled") is True
            and len(item.get("sources", [])) == 2
            and item["sources"][0]["locator"] == "/tmp/haochen-ui-qa/feedback.md"
        )
        if not passed:
            failure("real native save payload preserves chosen sources and opt-in", item)
        else:
            results.append({"check": "real native save payload preserves chosen sources and opt-in", "passed": True})

    native(verify_payload)
    click('[data-action="track"][data-id="qa-created"]')
    click('[data-action="track-edit"]')
    act(
        "const e=q('[name=goal]');e.value='QA fixture · 尚未保存的编辑';e.dispatchEvent(new "
        "Event('input',{bubbles:true}));return true;"
    )
    escape()
    check(
        "Esc returns editor to its parent detail",
        "return !q('#float-layer').hidden && q('#float-title').textContent.includes('事项详情');",
    )
    click('[data-action="track-edit"]')
    check(
        "reopening editor restores unsaved draft",
        "return q('[name=goal]').value==='QA fixture · 尚未保存的编辑';",
    )
    act("q('#track-form').requestSubmit();return true;", 550)
    check(
        "editing saves through native bridge and returns to original detail",
        "return q('#float-title').textContent.includes('事项详情') && "
        "q('#float-body').textContent.includes('QA fixture · 尚未保存的编辑');",
    )
    click('[data-action="track-delete-confirm"]')
    escape()
    check(
        "Esc dismisses deletion confirmation, not the parent detail",
        "return !q('.inline-confirm') && !q('#float-layer').hidden && !!q('[data-action=track-delete-confirm]');",
    )
    escape()
    native(lambda: window.send({"openReport": state["reports"][0]["date"]}), 500)
    check(
        "native menu opens the visible report dialog",
        "return !q('#float-layer').hidden && q('#float-title').textContent.includes('日报');",
    )
    act(
        "const b=q('#float-body');b.querySelector('details').open=true;b.scrollTop=240;window.__qaRep"
        "ortScroll=b.scrollTop;return true;"
    )
    shot("04-report-scroll")
    click('[data-action="track"][data-id="qa-track"]')
    escape()
    check(
        "nested detail restores report scroll and evidence disclosure",
        "return q('#float-title').textContent.includes('日报') && "
        "Math.abs(q('#float-body').scrollTop-window.__qaReportScroll)<2 && q('#float-body "
        "details').open;",
    )
    act(
        "q('#dashboard').dispatchEvent(new "
        "CompositionEvent('compositionstart',{bubbles:true}));document.dispatchEvent(new "
        "KeyboardEvent('keydown',{key:'Escape',bubbles:true,isComposing:true}));return true;",
        500,
    )
    check("IME Escape does not close report", "return !q('#float-layer').hidden;")
    act(
        "q('#dashboard').dispatchEvent(new "
        "CompositionEvent('compositionend',{bubbles:true}));document.dispatchEvent(new "
        "KeyboardEvent('keydown',{key:'Escape',bubbles:true}));return true;",
        500,
    )
    check("Escape immediately after IME end remains ignored", "return !q('#float-layer').hidden;")
    escape()
    check("later Escape closes report", "return q('#float-layer').hidden;")
    click('[data-action="connections"]')
    click('[data-action="calendar-list"]')
    check(
        "async calendar list renders actual response choices",
        "return qa('[data-calendar-id]').length===2;",
    )
    act(
        "const c=q('[data-calendar-id=qa-cal-2]');c.checked=true;c.dispatchEvent(new "
        "Event('change',{bubbles:true}));return true;"
    )
    click('[data-action="calendar-save"]')
    click('[data-action="palette"][data-palette="carbon"]')
    check(
        "palette persists via native state and changes text contrast",
        "return document.body.dataset.palette==='carbon' && "
        "getComputedStyle(document.body).color==='rgb(231, 235, 231)';",
    )
    shot("05-connections-carbon")
    escape()
    native(lambda: resize(520, 620), 150)
    check("narrow viewport is actually 520 points", "return innerWidth===520;")
    check(
        "narrow native panel avoids horizontal overflow",
        "return document.documentElement.scrollWidth<=innerWidth && q('#overview').scrollWidth<=innerWidth;",
    )
    shot("06-overview-narrow")
    click('[data-action="event"][data-id="qa-event"]')
    check(
        "narrow floating event fits within actual native panel",
        "const r=q('#float-window').getBoundingClientRect();return r.left>=0 && r.right<=innerWidth+1 "
        "&& r.top>=0 && r.bottom<=innerHeight+1;",
        timeout=6,
    )
    shot("07-event-narrow")
    click("#float-scrim")
    check("outside click closes one floating layer", "return q('#float-layer').hidden;")

    def no_spurious_collapse():
        if any(item["action"] == "collapse" for item in actions):
            failure("no modal Escape leaked into a main-window collapse", "unexpected collapse action")
        else:
            results.append({"check": "no modal Escape leaked into a main-window collapse", "passed": True})

    native(no_spurious_collapse)

    def empty_state():
        for name in ("events", "tracks", "calendar", "files", "reports"):
            state[name] = []
        for connector in state["connectors"]:
            connector.update(enabled=False, status="disabled")
        state["settings"]["palette"] = "sage"
        window.send({"state": copy.deepcopy(state)})

    native(lambda: resize(860, 680), 150)
    native(empty_state)
    check(
        "empty collections produce explicit connection and addition entry points",
        "return !qa('.event-card').length && !qa('.track-card').length && "
        "q('#event-list').textContent.includes('连接应用') && "
        "q('#track-list').textContent.includes('添加第一个事项');",
    )
    shot("08-empty-state")
    click('[data-action="track-new"]')
    act(
        "const f=q('#track-form');f.elements.title.value='QA fixture · 预期保存失败';"
        "f.elements.goal.value='验证失败可见且草稿不丢失';"
        "f.elements['source-locator-0'].value='https://example.com/qa-failure';"
        "f.dispatchEvent(new Event('input',{bubbles:true}));f.requestSubmit();return true;",
        500,
    )
    check(
        "native save failure stays visible and preserves user input",
        "const error=q('#track-form-error'),r=error.getBoundingClientRect(),"
        "body=q('#float-body').getBoundingClientRect();"
        "return !q('#float-layer').hidden && error.textContent.includes('测试保存错误') "
        "&& r.top>=body.top && r.bottom<=body.bottom && document.activeElement===error "
        "&& q('[name=title]').value==='QA fixture · 预期保存失败';",
    )
    shot("09-explicit-save-error")
    escape()

    window.message.connect(message)
    window.ready.connect(loaded)
    QTimer.singleShot(95000, lambda: failure("overall timeout", "95 seconds"))
    code = app.exec()
    return code


if __name__ == "__main__":
    sys.exit(main())
