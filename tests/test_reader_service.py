"""Worker lifecycle tests with synthetic content; never query the user's OS."""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from reader import haochen_reader as reader


def run_worker(monkeypatch, capsys, commands):
    monkeypatch.setattr(reader.sys, "stdin", io.StringIO("".join(json.dumps(c) + "\n" for c in commands)))
    assert reader.serve() == 0
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_startup_and_bind_never_read_content(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(reader, "check_accessibility", lambda **_: seen.append("permission") or True)
    monkeypatch.setattr(reader, "BoundTarget", lambda target: seen.append("metadata") or SimpleNamespace(target=target))
    monkeypatch.setattr(reader, "read_bound_snapshot", lambda *_: seen.append("body"))
    assert run_worker(monkeypatch, capsys, []) == [{"id": None, "event": "ready"}]
    assert seen == []
    events = run_worker(monkeypatch, capsys, [{"id": "a", "op": "bind", "target": {"pid": 20}},
                                             {"id": "a", "op": "cancel"}, {"id": "a", "op": "read"}])
    assert seen == ["permission", "metadata"]
    assert events[-1]["code"] == "binding_expired"


def test_only_bound_id_reads_and_images_finish_after_snapshot(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(reader, "check_accessibility", lambda **_: True)
    monkeypatch.setattr(reader, "BoundTarget", lambda target: SimpleNamespace(target=target))
    monkeypatch.setattr(reader, "read_bound_snapshot", lambda *_: (seen.append("body"), "pixels", True))
    def finish(*_):
        # stdout snapshot has already been emitted before network image fetches.
        assert '"event": "snapshot"' in capsys.readouterr().out
        seen.append("originals")
        return {"images_original": 15}
    monkeypatch.setattr(reader, "serialize_snapshot", finish)
    events = run_worker(monkeypatch, capsys, [{"id": "a", "op": "bind", "target": {"pid": 20}},
                                             {"id": "b", "op": "read"}])
    assert events[-1]["code"] == "binding_expired" and not seen
    events = run_worker(monkeypatch, capsys, [{"id": "a", "op": "bind", "target": {"pid": 20}},
                                             {"id": "a", "op": "read"}, {"id": "a", "op": "read"}])
    assert seen == ["body", "originals"]
    assert events[0]["event"] == "result" and events[0]["data"]["images_original"] == 15
    assert events[-1]["code"] == "binding_expired"
