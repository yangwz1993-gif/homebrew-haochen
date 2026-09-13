"""Bundled engine acceptance against loopback SSE; no live model or OS Keychain."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from haochen_app.dashboard import tracking  # noqa: E402
from haochen_app.keychain import MemoryCredentialStore  # noqa: E402
from haochen_app.settings.config_store import ConfigStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULT = "选定页面有一条新的验收结论。还需要用户确认，不能据此认定事项已经完成。"


@pytest.mark.parametrize(("configured_budget", "expected_budget", "cancel_before_register"), [
    (384_000, 4096, False), (128, 128, False), (384_000, 4096, True),
])
def test_real_engine_summary_isolated_selected_sources_and_bounded_tokens(
    tmp_path, monkeypatch, configured_budget, expected_budget, cancel_before_register,
):
    engine = ROOT / "engine" / "haochen-engine"
    if not engine.is_file() or not os.access(engine, os.X_OK):
        pytest.skip("bundled haochen-engine is required for the process-level acceptance test")

    requests, launches = [], []
    fake_key = "SYNTHETIC-SUMMARY-TEST-KEY"

    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or not 0 < size <= 1_000_000:
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(size))
            requests.append({"body": body, "selectedKey": self.headers.get("Authorization") == f"Bearer {fake_key}"})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (({"role": "assistant", "content": RESULT}, None), ({}, "stop")):
                chunk = {
                    "id": "synthetic-summary", "object": "chat.completion.chunk", "created": 1,
                    "model": "summary-fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = ConfigStore(tmp_path / "profile", keychain=MemoryCredentialStore())
    worker = tracking.SummaryWorker(config)
    worker.TIMEOUT_SECONDS = 15
    try:
        # Exercise the real settings contract, with a fake credential store and
        # a custom provider that cannot fall back to any built-in remote URL.
        provider, _ = config.upsert_custom_model(
            base_url=f"http://127.0.0.1:{server.server_port}/v1", model_id="summary-fixture",
            provider_id="custom-summary-fixture", key=fake_key,
        )
        catalog = config._load("models.json")
        model = catalog["providers"][provider]["models"][0]
        model.update(maxTokens=configured_budget, contextWindow=128_000,
                     compat={"supportsDeveloperRole": False}, thinkingLevelMap={"off": None})
        catalog["providers"][provider]["models"].append({
            "id": "unselected-model", "headers": {"X-Synthetic": "!DO-NOT-EXECUTE-UNSELECTED-MODEL"},
        })
        catalog["providers"]["unselected-provider"] = {"apiKey": "!DO-NOT-EXECUTE-UNSELECTED-PROVIDER"}
        config._save("models.json", catalog)
        config._save("auth.json", {provider: {"type": "api_key", "key": "!printf SYNTHETIC-SHARED-AUTH"}})

        extension_marker = tmp_path / "extension-was-loaded"
        extension = tmp_path / "synthetic-extension.ts"
        extension.write_text(
            'import { writeFileSync } from "node:fs";\n'
            f'export default function () {{ writeFileSync({json.dumps(str(extension_marker))}, "loaded"); }}\n',
            encoding="utf-8",
        )
        settings = config._load("settings.json")
        settings["extensions"] = [str(extension)]
        config._save("settings.json", settings)
        for directory in (config.agent_dir, config.home):
            (directory / "AGENTS.md").write_text("SYNTHETIC-OLD-AGENTS-MUST-NOT-APPEAR", encoding="utf-8")
        sessions = config.home / "pi-sessions"
        sessions.mkdir()
        old_session = sessions / "existing-chat.jsonl"
        old_session.write_text("SYNTHETIC-UNSELECTED-CHAT-HISTORY\n", encoding="utf-8")
        unrelated = config.home / "unselected-source.md"
        unrelated.write_text("SYNTHETIC-UNSELECTED-SOURCE", encoding="utf-8")
        initial_sessions = {str(p.relative_to(config.home)): p.read_bytes() for p in config.home.rglob("*.jsonl")}

        monkeypatch.setenv("OPENAI_API_KEY", "SYNTHETIC-WRONG-AMBIENT-KEY")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "SYNTHETIC-UNRELATED-AMBIENT-KEY")
        monkeypatch.setenv("NODE_OPTIONS", "--require /nonexistent-synthetic-summary-module")
        monkeypatch.setenv("PI_CODING_AGENT_DIR", str(config.agent_dir))
        monkeypatch.setenv("PI_SESSION_ID", "SYNTHETIC-UNSELECTED-CHAT")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        monkeypatch.setattr(tracking.paths, "engine_binary", lambda: engine)
        real_popen = tracking.subprocess.Popen

        def launch(argv, **kwargs):
            agent = Path(kwargs["env"]["PI_CODING_AGENT_DIR"])
            launches.append({
                "argv": argv, "environmentKeys": set(kwargs["env"]),
                "models": json.loads((agent / "models.json").read_text()),
                "settings": json.loads((agent / "settings.json").read_text()),
                "secretOnDisk": any(fake_key in p.read_text() for p in agent.iterdir() if p.is_file()),
            })
            return real_popen(argv, **kwargs)  # The actual bundled engine, not a substitute process.

        monkeypatch.setattr(tracking.subprocess, "Popen", launch)
        evidence = [{"sourceId": "selected-page", "label": "Selected webpage", "status": "ready",
                     "text": "SYNTHETIC-ONLY-SELECTED-PAGE: review completed, user confirmation pending."}]
        cancellation = threading.Event()
        if cancel_before_register:
            default_model = config.default_model

            def withdrawn_before_registration():
                selected_model = default_model()
                assert worker._active_cancel is None
                cancellation.set()
                worker.cancel()  # No active process yet; the external token must carry the cancellation.
                return selected_model

            monkeypatch.setattr(config, "default_model", withdrawn_before_registration)
            with pytest.raises(ValueError, match="取消"):
                worker.summarize("Synthetic acceptance", "Withdrawn evidence", evidence, cancel=cancellation)
            assert requests == launches == []
            assert worker._active_cancel is None
            return
        result = worker.summarize("Synthetic acceptance", "Check selected review feedback", evidence,
                                  cancel=cancellation)

        assert result == RESULT
        assert len(requests) == len(launches) == 1
        assert requests[0]["selectedKey"] is True
        body = requests[0]["body"]
        assert body["model"] == "summary-fixture" and body["stream"] is True
        assert not body.get("tools") and not body.get("functions")
        assert body.get("max_tokens", body.get("max_completion_tokens")) == expected_budget
        messages = json.dumps(body["messages"], ensure_ascii=False)
        assert "SYNTHETIC-ONLY-SELECTED-PAGE" in messages
        assert "Synthetic acceptance" in messages and "Check selected review feedback" in messages
        assert "SYNTHETIC-UNSELECTED" not in messages and "SYNTHETIC-OLD-AGENTS" not in messages
        assert "SYNTHETIC-SHARED-AUTH" not in messages
        assert all(m["role"] in {"system", "developer", "user"} for m in body["messages"])

        launch_data = launches[0]
        assert launch_data["argv"][0] == str(engine)
        for flag in ("--no-session", "--no-tools", "--no-extensions", "--no-skills", "--no-context-files"):
            assert flag in launch_data["argv"]
        assert not launch_data["secretOnDisk"]
        assert not launch_data["environmentKeys"] & {
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NODE_OPTIONS", "PI_SESSION_ID", "HTTPS_PROXY", "HOME",
        }
        assert set(launch_data["models"]["providers"]) == {provider}
        selected = launch_data["models"]["providers"][provider]
        assert [m["id"] for m in selected["models"]] == ["summary-fixture"]
        assert selected["models"][0]["maxTokens"] == expected_budget
        assert selected["models"][0]["contextWindow"] == 128_000
        assert selected["models"][0]["compat"] == {"supportsDeveloperRole": False}
        assert selected["models"][0]["thinkingLevelMap"] == {"off": None}
        assert launch_data["settings"]["extensions"] == []
        assert not extension_marker.exists()
        assert not list((config.home / "dashboard" / "summary-work").iterdir())
        final_sessions = {str(p.relative_to(config.home)): p.read_bytes() for p in config.home.rglob("*.jsonl")}
        assert final_sessions == initial_sessions
        assert config._load("models.json")["providers"][provider]["models"][0]["maxTokens"] == configured_budget
    finally:
        worker.stop()
        server.shutdown()
        server.server_close()
        thread.join(2)
