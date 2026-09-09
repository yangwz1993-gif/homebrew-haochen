"""Cover engine_client command surface and session helpers (P0 module ≥90%)."""

from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

engine_module = importlib.import_module("haochen_app.engine_client")
EngineClient = engine_module.EngineClient

RESPONDING_ENGINE = (
    "import json,sys\n"
    "for line in sys.stdin:\n"
    "    if not line.strip(): continue\n"
    "    cmd = json.loads(line)\n"
    "    print(json.dumps({'id':cmd['id'],'type':'response','command':cmd.get('type'),"
    "'success':True,'data':{}}), flush=True)\n"
)


def wait_until(predicate, timeout: float = 3.0, what: str = "") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timeout: {what}")


def make_client(tmp_path: Path) -> EngineClient:
    script = tmp_path / "engine.py"
    script.write_text("#!/usr/bin/env python3\n" + RESPONDING_ENGINE, encoding="utf-8")
    script.chmod(0o700)
    return EngineClient(engine=script, mock=True, home=tmp_path / "home")


def test_all_command_methods_round_trip(qtbot, tmp_path: Path) -> None:
    client = make_client(tmp_path)
    responses: list[dict] = []
    client.response.connect(responses.append)
    client.start()
    wait_until(lambda: client.alive)

    request_ids = [
        client.prompt("你好"),
        client.abort(),
        client.new_session(),
        client.switch_session("/sessions/a.jsonl"),
        client.get_messages(),
        client.get_state(),
        client.set_session_name("名字"),
        client.get_available_models(),
        client.set_model("deepseek", "m1"),
        client.respond_ui("ui-1", confirmed=True),
        client.respond_ui("ui-2", cancelled=True),
        client.respond_ui("ui-3", value="text"),
    ]

    qtbot.waitUntil(
        lambda: {r.get("id") for r in responses} >= set(request_ids), timeout=2000
    )
    commands = {r["id"]: r["command"] for r in responses}
    assert commands[request_ids[0]] == "prompt"
    assert commands[request_ids[2]] == "new_session"
    assert commands[request_ids[3]] == "switch_session"
    assert commands[request_ids[6]] == "set_session_name"
    assert commands[request_ids[7]] == "get_available_models"
    assert commands[request_ids[8]] == "set_model"

    client.stop()
    assert client.wait_stopped(timeout=2)


def test_list_sessions_orders_by_timestamp_desc(tmp_path: Path) -> None:
    home = tmp_path / "home"
    sessions = home / "pi-sessions"
    sessions.mkdir(parents=True)
    for name, ts, text in (
        ("a.jsonl", "2026-01-01", "最早"),
        ("b.jsonl", "2026-03-01", "最新"),
        ("c.jsonl", "2026-02-01", "中间"),
    ):
        (sessions / name).write_text(
            json.dumps({"type": "session", "id": name, "timestamp": ts})
            + "\n"
            + json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": text}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    result = engine_module.list_sessions(home)
    assert [r["id"] for r in result] == ["b.jsonl", "c.jsonl", "a.jsonl"]
    assert result[0]["preview"] == "最新"


def test_list_sessions_reads_persisted_session_title(tmp_path: Path) -> None:
    home = tmp_path / "home"
    sessions = home / "pi-sessions"
    sessions.mkdir(parents=True)
    (sessions / "named.jsonl").write_text(
        json.dumps({"type": "session", "id": "s", "timestamp": "2026-03-01"})
        + "\n"
        + json.dumps({"type": "session_info", "name": "东京夜游建议"})
        + "\n",
        encoding="utf-8",
    )

    result = engine_module.list_sessions(home)

    assert result[0]["title"] == "东京夜游建议"


def test_session_helpers_with_default_home(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "data"
    monkeypatch.setattr(engine_module, "haochen_home", lambda: home)
    sessions = home / "pi-sessions"
    sessions.mkdir(parents=True)
    session = sessions / "s.jsonl"
    session.write_text("{}", encoding="utf-8")

    deletion = engine_module.delete_session("s.jsonl")
    assert not session.exists()
    restored = engine_module.restore_session(deletion)
    assert restored == session.resolve()
    assert restored.is_file()
