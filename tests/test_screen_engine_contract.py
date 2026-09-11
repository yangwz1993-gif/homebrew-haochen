"""Real bundled engine + extension + synthetic reader/model. No user screen or keys."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from haochen_app import paths
from haochen_app.engine_client import EngineClient
from haochen_app.keychain import MemoryCredentialStore
from haochen_app.settings.config_store import ConfigStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("success", [False, True])
def test_read_reaches_model_and_engine_with_accurate_progress(qtbot, tmp_path, monkeypatch, success):
    engine = ROOT / "engine/haochen-engine"
    if not engine.is_file():
        pytest.skip("bundled engine required")
    requests = []
    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = {"role": "assistant", "content": "【brief】读取失败【/brief】【detail】读取失败【/detail】"}
            reason = "stop"
            if len(requests) == 1:
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "qa_read_call",
                         "type": "function", "function": {"name": "read_screen", "arguments": "{}"}}]}
                reason = "tool_calls"
            payload = {"id": "qa", "object": "chat.completion.chunk", "created": 1, "model": "qa",
                       "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
            payload["choices"] = [{"index": 0, "delta": {}, "finish_reason": reason}]
            self.wfile.write(("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode())
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    reader = tmp_path / "reader"
    read_reply = "r={'id':c['id'],'event':'error','message':'QA_CURRENT_READ_FAILURE'}"
    if success:
        read_reply = ("print(json.dumps({'id':c['id'],'event':'snapshot','elapsed_ms':5}),flush=True); "
                      "r={'id':c['id'],'event':'result','data':{'app':'QA','window_title':'Synthetic',"
                      "'stats':{'text_blocks':1,'images':0,'images_with_url':0},"
                      "'blocks':[{'kind':'text','text':'QA_CAPTURED_BODY'}],"
                      "'images_original':0,'images_unavailable':0,'screenshot':None}}")
    reader.write_text("#!/usr/bin/env python3\nimport json,sys\n"
                      "print(json.dumps({'event':'ready'}),flush=True)\n"
                      "for line in sys.stdin:\n"
                      " c=json.loads(line)\n"
                      " if c['op']=='bind': r={'id':c['id'],'event':'bound'}\n"
                      f" elif c['op']=='read': {read_reply}\n"
                      " else: continue\n"
                      " print(json.dumps(r),flush=True)\n")
    reader.chmod(0o700)
    monkeypatch.setattr(paths, "reader_binary", lambda: reader)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    client = EngineClient(mock=False, engine=engine, home=tmp_path)
    client.credentials = MemoryCredentialStore()
    store = ConfigStore(tmp_path, keychain=client.credentials)
    store.ensure_initialized()
    store._save("models.json", {"providers": {"qa": {
        "baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "api": "openai-completions",
        "models": [{"id": "qa", "name": "QA", "reasoning": False, "input": ["text"],
                    "contextWindow": 32768, "maxTokens": 128,
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}],
    }}})
    store._save("auth.json", {})
    store.set_key("qa", "test-only-key")
    store.set_default_model("qa", "qa")
    (tmp_path / "question-target.json").write_text(json.dumps({"token": "qa", "session": "qa", "target": {
        "pid": 20, "window_id": 40, "app": "QA", "title": "Synthetic", "fingerprint": "qa"}}))
    events = []
    def receive(event):
        events.append(event)
        if event.get("type") == "extension_ui_request" and event.get("method") == "confirm":
            client.respond_ui(event["id"], confirmed=True)
    client.event.connect(receive)
    try:
        client.start()
        client.prompt("测试这个页面")
        qtbot.waitUntil(lambda: any(e.get("type") == "agent_end" for e in events), timeout=20000)
        assert len(requests) == 2, events
        messages = requests[1]["messages"]
        results = [m for m in messages if m["role"] == "tool"]
        assert results and ("QA_CAPTURED_BODY" if success else "QA_CURRENT_READ_FAILURE") in json.dumps(results)
        assert "历史屏幕快照已封存" not in json.dumps(results, ensure_ascii=False)
        ended = [e for e in events if e.get("type") == "tool_execution_end"]
        assert len(ended) == 1 and ended[0]["isError"] is (not success)
        if success:
            phases = [e["partialResult"]["details"]["readPhase"] for e in events
                      if e.get("type") == "tool_execution_update"]
            assert phases == ["binding", "bound", "capturing", "snapshot"]
            assert ended[0]["result"]["details"]["readSuccess"] is True
    finally:
        client.stop()
        assert client.wait_stopped(3)
        server.shutdown()
        server.server_close()
        thread.join(2)
