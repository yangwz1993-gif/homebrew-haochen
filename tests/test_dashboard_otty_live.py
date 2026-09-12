"""Opt-in real Otty + real Pi test; isolated model/config and owned window only.

Run with HAOCHEN_OTTY_LIVE=1 after coordinating desktop focus. No global hooks,
private conversation reads, paid model calls, or existing terminal input.

For installed-app observation, set HAOCHEN_OTTY_RESPONSE_DELAY=15 and optionally
HAOCHEN_OTTY_POST_IDLE_SECONDS=30. Set HAOCHEN_OTTY_AUTO_FOCUS=0 when a reviewer
will click the app's own source button; the harness will not steal focus.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from haochen_app.dashboard.adapters.otty import OttyAdapter  # noqa: E402
from haochen_app.dashboard.adapters.otty_setup import OttyIntegrationSetup  # noqa: E402


@pytest.mark.skipif(os.environ.get("HAOCHEN_OTTY_LIVE") != "1", reason="explicit live desktop test only")
def test_real_pi_official_hook_reports_to_owned_otty_pane(tmp_path):
    cli = Path("/Applications/Otty.app/Contents/MacOS/otty-cli")
    pi = shutil.which("pi")
    node = shutil.which("node")
    if not cli.is_file() or not pi or not node:
        pytest.skip("local Otty and Pi required")
    response_delay = min(20.0, max(2.0, float(os.environ.get("HAOCHEN_OTTY_RESPONSE_DELAY", "2"))))
    post_idle_seconds = min(45.0, max(0.0, float(os.environ.get("HAOCHEN_OTTY_POST_IDLE_SECONDS", "0"))))
    auto_focus = os.environ.get("HAOCHEN_OTTY_AUTO_FOCUS", "1") != "0"
    from AppKit import NSWorkspace

    original = NSWorkspace.sharedWorkspace().frontmostApplication()

    def control(*args):
        result = subprocess.run([str(cli), "--json", "--timeout", "1500", *args],
                                capture_output=True, text=True, timeout=3)
        assert result.returncode == 0, "Otty control failed (output omitted)"
        value = json.loads(result.stdout)
        assert value.get("ok") is True
        return value["data"]

    requests = []

    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802
            size = int(self.headers.get("Content-Length", 0))
            if self.path != "/v1/chat/completions" or not 0 < size < 100_000:
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(size))
            requests.append({"model": body.get("model"), "tools": bool(body.get("tools"))})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            time.sleep(response_delay)  # Real in-flight loopback request; no external model.
            for delta, finish in (({"role": "assistant", "content": "Synthetic lifecycle check passed."}, None),
                                  ({}, "stop")):
                chunk = {"id": "synthetic", "object": "chat.completion.chunk", "created": 1,
                         "model": "lifecycle-fixture",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    isolated = tmp_path / "isolated"
    setup = OttyIntegrationSetup(cli, isolated)
    setup.setup("pi", apply=True)  # Only our brand new temp home, never ~/.pi.
    agent = isolated / ".pi/agent"
    extension = agent / "extensions/otty-integration.ts"
    (agent / "models.json").write_text(json.dumps({"providers": {"synthetic": {
        "baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "api": "openai-completions",
        "apiKey": "synthetic-local-only", "models": [{"id": "lifecycle-fixture", "name": "Lifecycle fixture",
        "reasoning": False, "input": ["text"], "contextWindow": 32000, "maxTokens": 64,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}],
    }}}))
    (agent / "settings.json").write_text(json.dumps({"packages": [], "extensions": [], "quietStartup": True}))
    output = tmp_path / "synthetic-rpc.jsonl"
    stop = tmp_path / "stop"
    argv = [node, str(Path(pi).resolve()), "--mode", "rpc", "--no-session", "--no-tools", "--no-extensions",
            "-e", str(extension), "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files",
            "--offline", "--provider", "synthetic", "--model", "lifecycle-fixture", "--thinking", "off",
            "--system-prompt", "This is an isolated local lifecycle check. Reply briefly."]
    environment = {"PATH": str(Path(node).parent) + ":/usr/bin:/bin", "PI_CODING_AGENT_DIR": str(agent),
                   "PI_OFFLINE": "1", "PI_TELEMETRY": "0", "TERM": "xterm-256color"}
    # A wrapper holds the RPC agent alive after its turn, so idle can be checked
    # before its process exits. It never types into any pre-existing pane.
    runner = tmp_path / "owned-runner.py"
    runner.write_text(
        "import json,subprocess,time,pathlib,os\n"
        f"stop=pathlib.Path({str(stop)!r})\n"
        f"environment={environment!r}\n"
        "if os.environ.get('OTTY_SOCKET'): environment['OTTY_SOCKET']=os.environ['OTTY_SOCKET']\n"
        f"with open({str(output)!r},'w') as out:\n"
        f" child=subprocess.Popen({argv!r},env=environment,stdin=subprocess.PIPE,stdout=out,stderr=out)\n"
        " try:\n"
        "  time.sleep(3)  # Allow Otty's process-tree discovery to attach this new agent.\n"
        "  prompt=json.dumps({'type':'prompt','message':'Run the synthetic lifecycle check.'})+'\\n'\n"
        "  child.stdin.write(prompt.encode())\n"
        "  child.stdin.flush()\n"
        f"  deadline=time.monotonic()+{response_delay + post_idle_seconds + 35}\n"
        "  while child.poll() is None and not stop.exists() and time.monotonic()<deadline: time.sleep(.1)\n"
        " finally:\n"
        "  if child.poll() is None: child.terminate()\n"
        "  try: child.wait(timeout=3)\n"
        "  except subprocess.TimeoutExpired: child.kill();child.wait()\n"
    )
    owned_window = None
    observed, focused = [], False
    previous_windows = {row["id"] for row in control("window", "list")}
    try:
        control("window", "new", "--no-focus", "--title", "haochen · isolated lifecycle check",
                "--cwd", str(tmp_path), "--command", shlex.join([sys.executable, str(runner)]))
        # Otty 1.4.1 returns the text "Window created", not a window identifier.
        # Bind only a newly created window whose pane has our unique test cwd.
        for _ in range(20):
            owned = {row["window_id"] for row in control("pane", "list")
                     if row.get("cwd") == str(tmp_path) and row["window_id"] not in previous_windows}
            if len(owned) == 1:
                owned_window = owned.pop()
                break
            time.sleep(0.1)
        assert isinstance(owned_window, str) and owned_window.startswith("w_")
        print(f"otty-live: owned window {owned_window}; waiting for real Pi state", flush=True)
        target = None
        deadline = time.monotonic() + response_delay + 23
        while time.monotonic() < deadline:
            rows = [row for row in control("pane", "list") if row.get("window_id") == owned_window]
            for row in rows:
                state = row.get("agent_state")
                if state in {"processing", "idle"} and (not observed or observed[-1] != state):
                    observed.append(state)
                    print(f"otty-live: owned pane state={state}", flush=True)
                if state == "idle" and "processing" in observed and row.get("agent_session_id"):
                    target = {"kind": "otty", "paneId": row["id"], "tabId": row["tab_id"],
                              "windowId": row["window_id"]}
            if target:
                break
            time.sleep(0.12)
        assert target is not None, f"Owned lifecycle not observed; states={observed}, requestCount={len(requests)}"
        assert requests == [{"model": "lifecycle-fixture", "tools": False}]
        assert "Synthetic lifecycle check passed." in output.read_text()
        if post_idle_seconds:
            print(f"otty-live: holding own idle pane for {post_idle_seconds:g}s for installed-app review",
                  flush=True)
            time.sleep(post_idle_seconds)
        if auto_focus:
            OttyAdapter(cli).focus(target)
            rows = control("window", "list")
            focused = any(row["id"] == owned_window and row.get("focused") for row in rows)
            assert focused
        focus_note = "precise owned pane focus confirmed" if focused else "automatic focus intentionally skipped"
        print("otty-live: real Pi + official hook: processing -> idle; " + focus_note, flush=True)
    finally:
        stop.touch()
        time.sleep(0.3)
        if owned_window and owned_window not in previous_windows:
            control("window", "close", "--window", owned_window, "--force")
        if original:
            original.activateWithOptions_(0)
        server.shutdown()
        server.server_close()
    current = {row["id"] for row in control("window", "list")}
    assert previous_windows <= current and owned_window not in current
