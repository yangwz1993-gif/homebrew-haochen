"""Only isolated temp homes, synthetic pages and stub Chrome; never personal tabs."""

from __future__ import annotations

import io
import json
import os
import select
import shutil
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from haochen_app.dashboard.adapters.browser import BrowserAdapter, iso_time  # noqa: E402
from haochen_app.dashboard.browser_host import (  # noqa: E402
    MAX_CONTENT,
    MAX_FOCUS_COMMANDS,
    MAX_FRAME,
    MAX_SOURCES,
    STALE_AFTER,
    BrowserStore,
    PrivateSignal,
    bridge_directory,
    read_frame,
    run_browser_host,
    valid_url,
    write_frame,
)

EXT = "a" * 32
CLIENT = "synthetic-client-001"
SOURCE = "synthetic-source-001"


def observation(**changes):
    value = {
        "type": "observation",
        "sourceId": SOURCE,
        "url": "https://example.com/report",
        "title": "Synthetic report",
        "content": "Progress: completed one check.",
        "status": "available",
        "coverage": "main_frame_text",
        "tabId": 7,
        "windowId": 2,
    }
    return value | changes


def frames(*values):
    stream = io.BytesIO()
    for value in values:
        write_frame(stream, value)
    stream.seek(0)
    return stream


def configured(tmp_path):
    home = tmp_path / "haochen home"
    adapter = BrowserAdapter(home)
    result = adapter.install_host(EXT, chrome_home=tmp_path / "fake chrome", command=[sys.executable, "--browser-host"])
    return home, adapter, result


def test_frame_roundtrip_and_limits():
    assert read_frame(frames({"text": "你好"})) == {"text": "你好"}
    assert read_frame(io.BytesIO()) is None
    for payload in (
        b"a",
        struct.pack("=I", MAX_FRAME + 1),
        struct.pack("=I", 0),
        struct.pack("=I", 3) + b"{}",
        struct.pack("=I", 2) + b"[]",
    ):
        with pytest.raises(ValueError):
            read_frame(io.BytesIO(payload))


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://u:p@example.com/",
        "https://example.com:bad/",
        "https://example.com/\nsecret",
        "",
        None,
    ],
)
def test_url_validation_never_fetches(url):
    with pytest.raises((ValueError, TypeError)):
        valid_url(url)
    assert valid_url("http://localhost:8080/explicitly-selected")  # Browser read, not an SSRF fetch.


def test_empty_adapter_is_honest(tmp_path):
    result = BrowserAdapter(tmp_path).snapshot()
    assert result["status"] == "not_connected" and result["events"] == []
    assert result["checkedAt"].endswith("Z")
    assert BrowserAdapter(tmp_path).evidence("https://example.com/not-selected")["status"] == "not_watched"


def test_real_observation_dedupe_dates_stale_disconnect_and_url_lookup(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setattr("haochen_app.dashboard.adapters.browser.time.time", lambda: now)
    store = BrowserStore(tmp_path)
    session = store.connect(CLIENT, now)
    try:
        store.observe(observation(), session, CLIENT, now - 10)
        adapter = BrowserAdapter(tmp_path)
        evidence = adapter.evidence({"locator": "https://example.com/report"})
        assert evidence["content"] == observation()["content"] and evidence["status"] == "available"
        assert evidence["untrusted"] is True
        first_update = evidence["updatedAt"]
        store.observe(observation(), session, CLIENT, now)
        result = adapter.snapshot()
        assert result["status"] == "connected"
        event = result["events"][0]
        assert event["sourceId"] == SOURCE and event["id"] == "browser:" + SOURCE
        assert event["updatedAt"] == first_update and event["checkedAt"] == iso_time(now)
        assert event["target"]["navigation"] == "exact_tab"
        assert event["target"]["sourceId"] == SOURCE
        assert event["target"]["clientId"] == CLIENT
        assert event["target"]["sessionId"] == session
        store.observe(observation(content="New genuine difference"), session, CLIENT, now)
        assert adapter.evidence(SOURCE)["updatedAt"] == iso_time(now)
        now += STALE_AFTER + 1
        store.ping(session, now)
        assert adapter.evidence(SOURCE)["status"] == "stale"
        assert adapter.evidence(SOURCE)["content"] == ""
        store.disconnect(session)
        assert adapter.evidence(SOURCE)["status"] == "disconnected"
    finally:
        store.close()


@pytest.mark.parametrize(
    "state", ["permission_required", "tab_closed", "suspended", "target_changed", "error", "reading"]
)
def test_failure_states_never_leak_old_evidence(tmp_path, state):
    store = BrowserStore(tmp_path)
    try:
        session = store.connect(CLIENT)
        store.observe(observation(), session, CLIENT)
        store.observe(observation(status=state), session, CLIENT)
        evidence = BrowserAdapter(tmp_path).evidence(SOURCE)
        assert evidence["status"] == state and evidence["content"] == "" and evidence["coverage"] == "none"
        assert store.records()[0]["content"] == ""
    finally:
        store.close()


def test_source_cannot_silently_navigate_or_change_owner(tmp_path):
    store = BrowserStore(tmp_path)
    try:
        session = store.connect(CLIENT)
        store.observe(observation(), session, CLIENT)
        with pytest.raises(ValueError, match="identity"):
            store.observe(observation(url="https://example.com/other"), session, CLIENT)
        with pytest.raises(ValueError, match="identity"):
            store.observe(observation(), session, "different-client-001")
        for fields in (
            {"content": "x" * (MAX_CONTENT + 1)},
            {"sourceId": "../bad"},
            {"status": "invented"},
            {"coverage": "all_browser_data"},
            {"tabId": True},
        ):
            with pytest.raises(ValueError):
                store.observe(observation(**fields), session, CLIENT)
        store.forget(SOURCE, "different-client-001")
        assert len(store.records()) == 1
        store.forget(SOURCE, CLIENT)
        assert store.records() == []
    finally:
        store.close()


def test_bounded_storage_and_private_modes(tmp_path):
    store = BrowserStore(tmp_path)
    try:
        session = store.connect(CLIENT)
        for index in range(MAX_SOURCES):
            store.observe(observation(sourceId=f"source-{index:04d}"), session, CLIENT)
        with pytest.raises(ValueError, match="too many"):
            store.observe(observation(sourceId="source-overflow"), session, CLIENT)
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.path.parent.stat().st_mode & 0o777 == 0o700
        store.connect("new-client-001", time.time() + 8 * 86400)
        assert store.records() == []
    finally:
        store.close()


def test_reject_symlink_storage(tmp_path):
    directory = bridge_directory(tmp_path)
    victim = tmp_path / "untouched.txt"
    victim.write_text("untouched")
    (directory / "observations.sqlite3").symlink_to(victim)
    with pytest.raises(ValueError):
        BrowserStore(tmp_path)
    assert victim.read_text() == "untouched"


def test_install_is_explicit_validated_and_uninstall_scoped(tmp_path):
    adapter = BrowserAdapter(tmp_path / "data")
    for invalid in ("b" * 31, "z" * 32, "a" * 32 + "; touch nope", None):
        with pytest.raises(ValueError):
            adapter.install_host(invalid, chrome_home=tmp_path / "fake chrome")
    home, adapter, result = configured(tmp_path)
    manifest_path = Path(result["manifestPath"])
    manifest = json.loads(manifest_path.read_text())
    assert manifest["allowed_origins"] == [f"chrome-extension://{EXT}/"]
    launcher = Path(manifest["path"])
    assert "HAOCHEN_HOME=" in launcher.read_text() and '"$@"' in launcher.read_text()
    assert launcher.stat().st_mode & 0o777 == 0o700
    foreign = manifest_path.parent / "unrelated.json"
    foreign.write_text("other-app")
    adapter.uninstall_host(chrome_home=tmp_path / "fake chrome")
    assert not manifest_path.exists() and not launcher.exists()
    assert foreign.read_text() == "other-app"


def test_native_protocol_origin_handshake_ownership_and_eof(tmp_path):
    home, adapter, _ = configured(tmp_path)
    output = io.BytesIO()
    assert run_browser_host(home, ["chrome-extension://" + "b" * 32 + "/"], frames(), output) == 2
    request = frames({"type": "hello", "version": 1, "clientId": CLIENT}, observation(), {"type": "ping"})
    assert run_browser_host(home, [f"chrome-extension://{EXT}/"], request, output) == 0
    output.seek(0)
    assert [read_frame(output)["operation"] for _ in range(3)] == ["hello", "observation", "ping"]
    assert adapter.evidence(SOURCE)["status"] == "disconnected"  # EOF is a disconnect, not live.
    output = io.BytesIO()
    assert run_browser_host(home, [f"chrome-extension://{EXT}/"], frames(observation()), output) == 1
    assert b"Progress:" not in output.getvalue()


def test_native_forget_ack_is_correlated(tmp_path):
    home, adapter, _ = configured(tmp_path)
    output = io.BytesIO()
    request = frames(
        {"type": "hello", "version": 1, "clientId": CLIENT}, observation(), {"type": "forget", "sourceId": SOURCE}
    )
    assert run_browser_host(home, [f"chrome-extension://{EXT}/"], request, output) == 0
    output.seek(0)
    read_frame(output)
    read_frame(output)
    assert read_frame(output)["sourceId"] == SOURCE
    assert adapter.evidence(SOURCE)["status"] == "not_watched"


def test_native_windowed_binary_stream_fallback(tmp_path, monkeypatch):
    home, _, _ = configured(tmp_path)
    input_stream = frames({"type": "hello", "version": 1, "clientId": CLIENT})
    output_stream = io.BytesIO()
    monkeypatch.setattr("haochen_app.dashboard.browser_host.sys.stdin", None)
    monkeypatch.setattr("haochen_app.dashboard.browser_host.sys.stdout", None)
    monkeypatch.setattr("haochen_app.dashboard.browser_host.os.dup", lambda fd: fd + 20)
    created = []

    def fake_fdopen(fd, mode):
        created.append((fd, mode))
        return input_stream if fd == 20 else output_stream

    monkeypatch.setattr("haochen_app.dashboard.browser_host.os.fdopen", fake_fdopen)
    assert run_browser_host(home, [f"chrome-extension://{EXT}/"]) == 0
    assert created == [(20, "rb"), (21, "wb")]
    assert input_stream.closed and output_stream.closed


def test_actual_app_entrypoint_uses_native_stdio_not_qt(tmp_path):
    home, adapter, _ = configured(tmp_path)
    request = frames({"type": "hello", "version": 1, "clientId": CLIENT}, observation()).getvalue()
    env = dict(os.environ, HAOCHEN_HOME=str(home))
    result = subprocess.run(
        [sys.executable, str(ROOT / "app/run_app.py"), "--browser-host", f"chrome-extension://{EXT}/"],
        input=request,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    output = io.BytesIO(result.stdout)
    assert read_frame(output)["operation"] == "hello"
    assert read_frame(output)["operation"] == "observation"
    assert read_frame(output) is None
    assert not (home / "logs").exists()  # GUI startup/logging did not happen.
    assert adapter.snapshot()["events"][0]["title"] == "Synthetic report"


def test_focus_requires_unchanged_explicit_binding_and_live_session(tmp_path):
    adapter, store = BrowserAdapter(tmp_path), BrowserStore(tmp_path)
    try:
        session = store.connect(CLIENT)
        store.observe(observation(), session, CLIENT)
        target = adapter.snapshot()["events"][0]["target"]
        for field, replacement in (
            ("sourceId", "untracked-source"),
            ("clientId", "other-profile"),
            ("sessionId", "old-session"),
            ("tabId", 8),
            ("windowId", 3),
            ("url", "https://example.com/other"),
            ("tabId", True),
        ):
            result = adapter.focus(target | {field: replacement})
            assert not result["ok"] and result["status"] in {"target_changed", "not_watched"}
        assert adapter.focus(target)["status"] == "disconnected"  # No host listening.
        assert adapter.open_target(target)["mode"] == "url_fallback"  # Separate opt-in only.
        for state in ("tab_closed", "suspended", "target_changed", "permission_required"):
            store.observe(observation(status=state), session, CLIENT)
            assert adapter.focus(target)["status"] == state
        store.observe(observation(), session, CLIENT)
        store.disconnect(session)
        assert adapter.focus(target)["status"] == "disconnected"
        assert not list(store.path.parent.glob("focus-*.fifo"))
    finally:
        store.close()


def test_focus_queue_is_bounded_revalidated_and_profile_scoped(tmp_path):
    store = BrowserStore(tmp_path)
    try:
        session = store.connect(CLIENT)
        store.observe(observation(), session, CLIENT)
        target = BrowserAdapter(tmp_path).snapshot()["events"][0]["target"]
        for index in range(MAX_FOCUS_COMMANDS):
            assert store.queue_focus(f"focus-command-{index}", target, time.time() + 3) is None
        assert store.queue_focus("focus-overflow", target, time.time() + 3) == "busy"
        other_session = store.connect("other-profile-001")
        assert store.take_focus(other_session) == []
        commands = store.take_focus(session)
        assert len(commands) == MAX_FOCUS_COMMANDS
        assert all(command["type"] == "focus" and command["target"] == target for command in commands)
        command_id = commands[0]["commandId"]
        store.finish_focus(command_id, other_session, "focused")
        assert store.focus_result(command_id) is None
        store.cancel_focus(command_id)
        store.finish_focus(command_id, session, "focused")
        assert store.focus_result(command_id) == "timeout"
        store.set_enabled(False)
        assert store.records() == []
        assert store.focus_result(commands[1]["commandId"]) == "disabled"
        store.set_enabled(True)
        with pytest.raises(ValueError, match="revoked"):
            store.observe(observation(), session, CLIENT)
        with pytest.raises(ValueError, match="revoked"):
            store.ping(session)
        session = store.connect(CLIENT)
        store.observe(observation(), session, CLIENT)
        fresh = BrowserAdapter(tmp_path).snapshot()["events"][0]["target"]
        assert store.queue_focus("focus-old-session", target, time.time() + 3) == "target_changed"
        assert store.queue_focus("focus-new-session", fresh, time.time() + 3) is None
        store.forget(SOURCE, CLIENT)
        assert store.take_focus(session) == []
        assert store.focus_result("focus-new-session") == "not_watched"
    finally:
        store.close()


def test_focus_timeout_has_no_late_dispatch_and_fifo_is_private(tmp_path):
    store, adapter = BrowserStore(tmp_path), BrowserAdapter(tmp_path)
    try:
        session = store.connect(CLIENT)
        store.observe(observation(), session, CLIENT)
        target = adapter.snapshot()["events"][0]["target"]
        wake = PrivateSignal(store.path.parent, "host-" + session)
        try:
            assert wake.path.stat().st_mode & 0o777 == 0o600
            assert adapter.focus(target, timeout=0.1)["status"] == "timeout"
            assert store.take_focus(session) == []  # Cancelled, no late tab activation.
        finally:
            wake.close()
    finally:
        store.close()


def test_real_native_process_focus_without_heartbeat_and_ack(tmp_path):
    """Real app-entry subprocess/OS pipes/FIFOs, but a synthetic Chrome peer only."""
    home, adapter, _ = configured(tmp_path)
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app/run_app.py"), "--browser-host", f"chrome-extension://{EXT}/"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ, HAOCHEN_HOME=str(home)),
        bufsize=0,
    )

    def receive():
        assert select.select([process.stdout], [], [], 3)[0], "native host response timed out"
        return read_frame(process.stdout)

    try:
        # Fragment a frame header, then send multiple frames together. The host
        # must not strand buffered messages while selecting on the OS pipe.
        request = frames({"type": "hello", "version": 1, "clientId": CLIENT}, observation()).getvalue()
        process.stdin.write(request[:2])
        process.stdin.write(request[2:])
        hello = receive()
        assert hello["operation"] == "hello" and hello["sessionId"]
        assert receive()["operation"] == "observation"
        target = adapter.snapshot()["events"][0]["target"]
        with ThreadPoolExecutor(max_workers=1) as worker:
            started = time.monotonic()
            pending = worker.submit(adapter.focus, target, 2)
            command = receive()  # No ping/observation sent to wake the idle host.
            assert command["type"] == "focus" and command["target"] == target
            assert time.monotonic() - started < 1.5
            write_frame(process.stdin, {"type": "focus_result", "commandId": command["commandId"], "status": "focused"})
            assert pending.result(timeout=3)["status"] == "focused"
            assert receive()["operation"] == "focus_result"
            pending = worker.submit(adapter.focus, target, 2)
            command = receive()
            # Failures come from the exact extension instance and never become URL fallback.
            write_frame(
                process.stdin, {"type": "focus_result", "commandId": command["commandId"], "status": "suspended"}
            )
            result = pending.result(timeout=3)
            assert result["status"] == "suspended" and not result["ok"] and result["mode"] == "exact_tab"
            assert receive()["operation"] == "focus_result"
            pending = worker.submit(adapter.focus, target, 2)
            assert receive()["type"] == "focus"
            process.stdin.close()
            assert pending.result(timeout=3)["status"] == "disconnected"
        assert process.wait(timeout=3) == 0
        assert not list(bridge_directory(home).glob("*.fifo"))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def test_extension_permission_surface():
    manifest = json.loads((ROOT / "browser-extension/manifest.json").read_text())
    assert "host_permissions" not in manifest
    assert "tabs" not in manifest["permissions"]  # No all-tab title/URL access at installation.
    assert manifest["optional_host_permissions"] == ["https://*/*", "http://*/*"]
    assert "connect-src 'none'" in manifest["content_security_policy"]["extension_pages"]
    assert "content_scripts" not in manifest  # No blanket page injection.


def test_extension_stub_exact_page_revocation_no_implicit_rebind():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for Chrome stub")
    script = r"""
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const events={}, posts=[], injections=[], saved={}, focuses=[];
const event=name=>({addListener(fn){events[name]=fn;}});
let allowed=true, tab={id:7,windowId:2,url:'https://example.com/report',title:'Synthetic',status:'complete'};
const origin='chrome-extension://'+'a'.repeat(32)+'/';
const nativeSession='12345678-abcd-4abc-abcd-123456789012';
const chrome={storage:{local:{async get(){return {};},async set(v){Object.assign(saved,v);}}},
 runtime:{id:'a'.repeat(32),getURL:p=>origin+p,onMessage:event('message'),onStartup:event('startup'),onInstalled:event('installed'),
  connectNative(){return {onMessage:event('nativeMessage'),onDisconnect:event('disconnect'),
   postMessage(m){posts.push(m);if(m.type==='hello')queueMicrotask(()=>events.nativeMessage({type:'ack',operation:'hello',sessionId:nativeSession}));},disconnect(){}};}},
 permissions:{async contains(){return allowed;},async request(){return allowed;},onRemoved:event('revoked')},
 tabs:{async get(){if(!tab)throw Error('closed');return {...tab};},
   async update(id,opts){assert.equal(id,tab.id);assert.deepEqual(opts,{active:true});
     focuses.push(['tab',id]);tab.active=true;return {...tab};},
   onRemoved:event('removed'),onUpdated:event('updated')},
 windows:{async update(id,opts){assert.equal(id,tab.windowId);assert.deepEqual(opts,{focused:true});
   focuses.push(['window',id]);return {id,focused:true};}},
 scripting:{async executeScript(v){injections.push(v);}},
 alarms:{onAlarm:event('alarm'),async create(){}}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{chrome,URL,crypto:require('crypto').webcrypto,
 setTimeout,clearTimeout,setInterval,clearInterval,queueMicrotask});
const wait=()=>new Promise(r=>setImmediate(r));
const call=(m,s={url:origin+'popup.html'})=>new Promise(r=>events.message(m,s,r));
let commandIndex=0;
const focus=async target=>{const commandId='focus-test-'+(++commandIndex);
 events.nativeMessage({type:'focus',commandId,expiresAt:Date.now()+2000,target});await wait();await wait();
 return posts.find(x=>x.type==='focus_result'&&x.commandId===commandId)?.status;};
(async()=>{await wait();
 assert((await call({type:'requestTrack',tabId:7,windowId:2,url:tab.url})).ok);await wait();
 let state=await call({type:'state'});assert.equal(state.sources.length,1);
 const id=state.sources[0].id;
 assert(injections.every(x=>x.target.tabId===7&&x.target.frameIds[0]===0&&x.world==='ISOLATED'));
 const capture={type:'captured',sourceId:id,url:tab.url,title:'Synthetic',content:'public test',
   coverage:'main_frame_text'};
 assert(!(await call(capture,{frameId:1,tab:{id:7},url:tab.url})).ok);
 assert(!(await call(capture,{frameId:0,tab:{id:8},url:tab.url})).ok);
 assert((await call(capture,{frameId:0,tab:{id:7},url:tab.url})).ok);
 assert(posts.some(x=>x.type==='observation'&&x.content==='public test'));
 assert(!JSON.stringify(saved).includes('public test')); // Body never stored by extension.
 const target={kind:'browser',sourceId:id,clientId:saved.clientId,sessionId:nativeSession,
   tabId:7,windowId:2,url:tab.url};
 assert.equal(await focus(target),'focused');assert.deepEqual(focuses,[['tab',7],['window',2]]);
 for(const [key,value] of [['sourceId','not-watched'],['clientId','other-profile'],['sessionId','old-session'],
   ['tabId',8],['windowId',3],['url','https://example.com/unselected']]){
   assert.notEqual(await focus({...target,[key]:value}),'focused');
 }
 assert.equal(focuses.length,2); // No arbitrary tab/profile/URL commands executed.
 assert(!(await call({type:'focus',sourceId:id,target},{frameId:0,tab:{id:7},url:tab.url})).ok);
 assert(!(await call({type:'focus',sourceId:id,target})).ok); // Popup is not the native click channel.
 events.nativeMessage({type:'focus',commandId:'focus-expired-001',expiresAt:Date.now()-1,target});await wait();
 assert.equal(posts.find(x=>x.commandId==='focus-expired-001').status,'timeout');
 allowed=false;assert.equal(await focus(target),'permission_required');allowed=true;
 tab.discarded=true;assert.equal(await focus(target),'suspended');tab.discarded=false;
 tab.frozen=true;assert.equal(await focus(target),'suspended');tab.frozen=false;
 const previousTab=tab;tab=null;assert.equal(await focus(target),'tab_closed');tab=previousTab;
 assert.equal(focuses.length,2);
 const before=injections.length;tab.url='https://example.com/elsewhere';await events.updated(7,{url:tab.url});
 assert.equal(await focus(target),'target_changed');assert.equal(focuses.length,2);
 assert.equal(injections.length,before);state=await call({type:'state'});
 assert.equal(state.sources[0].status,'target_changed');
 assert(!(await call({...capture,url:tab.url},{frameId:0,tab:{id:7},url:tab.url})).ok);
 allowed=false;await events.revoked();state=await call({type:'state'});
 assert.equal(state.sources[0].status,'permission_required');
 assert(!(await call(capture,{frameId:0,tab:{id:7},url:capture.url})).ok);
 assert((await call({type:'remove',sourceId:id})).ok);state=await call({type:'state'});
 assert.equal(state.sources.length,0);
 assert(posts.some(x=>x.type==='forget'&&x.sourceId===id));
 console.log('Chrome stub checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script, str(ROOT / "browser-extension/background.js")], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert "Chrome stub checks passed" in result.stdout


def test_extension_permission_transaction_survives_popup_teardown():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for Chrome permission regression")
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests/browser-extension-permissions.test.mjs")],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
