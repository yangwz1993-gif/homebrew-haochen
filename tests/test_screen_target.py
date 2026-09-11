"""Deterministic window switching/consent races; no user screen or credentials read."""
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
app_tracking = importlib.import_module("haochen_app.app_tracking")
session_coordinator = importlib.import_module("haochen_app.session_coordinator")

reader = importlib.import_module("reader.haochen_reader")
target_module = importlib.import_module("reader.screen_target")


def target(pid=20, wid=40):
    return {"pid": pid, "window_id": wid, "app": "Test", "title": "Page A", "fingerprint": "a"}


def setup_window(monkeypatch, blocks=None):
    monkeypatch.setattr(reader, "matching_ax_window", lambda pid, wid: "A" if (pid, wid) == (20, 40) else None)
    monkeypatch.setattr(reader, "fingerprint", lambda _: "a")
    monkeypatch.setattr(reader, "_copy", lambda *_: "Page A")
    monkeypatch.setattr(reader, "_geom", lambda _: (0, 0))
    monkeypatch.setattr(reader, "_size", lambda _: (600, 400))
    monkeypatch.setattr(reader, "_has_webarea", lambda _: True)
    monkeypatch.setattr(reader, "_walk", lambda _w, _d, out: out.extend(
        blocks if blocks is not None else [reader.Block("text", text="正文" * 100)]))


def test_queue_binds_at_question_not_dispatch(monkeypatch, tmp_path):
    monkeypatch.setattr(session_coordinator, "capture_question_target", lambda _: target())
    coordinator = session_coordinator.SessionCoordinator(tmp_path)
    first = coordinator.enqueue("这个页面说啥", "pet")
    monkeypatch.setattr(session_coordinator, "capture_question_target", lambda _: target(30, 50))
    second = coordinator.enqueue("现在这个呢", "chat")
    app_tracking.publish_question_target(tmp_path, first, "session-a")
    bound = json.loads((tmp_path / "question-target.json").read_text())
    assert bound["target"] == target()
    assert second.screen_target == target(30, 50)
    assert (tmp_path / "question-target.json").stat().st_mode & 0o777 == 0o600
    # A process restart cannot re-use an old OS PID/window identity.
    assert session_coordinator.SessionCoordinator(tmp_path).queue[0].screen_target is None


def test_unknown_target_published_as_unknown_not_old_value(tmp_path):
    item = session_coordinator.QueueItem("id", "hi", "pet")
    app_tracking.publish_question_target(tmp_path, item, None)
    assert json.loads((tmp_path / "question-target.json").read_text())["target"] is None


def test_bound_window_not_frontmost_and_text_needs_no_screenshot(monkeypatch):
    setup_window(monkeypatch)
    monkeypatch.setattr(reader, "frontmost_pid", lambda: pytest.fail("must not select foreground"))
    monkeypatch.setattr(reader, "capture_window_image", lambda *_a, **_k: pytest.fail("text does not need screenshot"))
    content, shot, visual = reader.read_bound_snapshot(target())
    assert content.window_title == "Page A" and content.blocks[0].text.startswith("正文")
    assert shot is None and not visual


@pytest.mark.parametrize("change", ["closed", "page", "unidentified"])
def test_changed_or_unidentified_target_fails_before_body(monkeypatch, change):
    setup_window(monkeypatch)
    bound = target()
    if change == "closed":
        monkeypatch.setattr(reader, "matching_ax_window", lambda *_: None)
    elif change == "page":
        monkeypatch.setattr(reader, "fingerprint", lambda _: "new-page")
    else:
        bound["fingerprint"] = ""
    monkeypatch.setattr(reader, "_walk", lambda *_: pytest.fail("must refuse before body access"))
    with pytest.raises(reader.AXError):
        reader.read_bound_snapshot(bound)


def test_body_changes_during_snapshot_are_discarded(monkeypatch):
    setup_window(monkeypatch)
    values = iter(["A" * 100, "B" * 100])
    monkeypatch.setattr(reader, "_walk", lambda _w, _d, out: out.append(reader.Block("text", text=next(values))))
    with pytest.raises(reader.AXError, match="采集期间"):
        reader.read_bound_snapshot(target())


def test_visual_snapshot_captured_before_original_downloads(monkeypatch):
    setup_window(monkeypatch, [reader.Block("image", url="https://example.com/original.png")])
    seen = []
    monkeypatch.setattr(reader, "capture_window_image", lambda *args, **kwargs: seen.append(kwargs) or "snapshot")
    monkeypatch.setattr(reader, "prepare_images", lambda _: pytest.fail("network must happen later"))
    content, shot, visual = reader.read_bound_snapshot(target())
    assert shot == "snapshot" and visual
    assert seen == [{"window_id": 40}]
    assert content.blocks[0].url.endswith("original.png")


def test_metadata_same_title_different_document(monkeypatch):
    monkeypatch.setattr(target_module, "attribute", lambda *_: "same title")
    monkeypatch.setattr(target_module, "document_id", lambda window: window)
    assert target_module.fingerprint("https://example.com/a") != target_module.fingerprint("https://example.com/b")


def test_metadata_does_not_request_permission_or_read_body(monkeypatch):
    calls = []
    monkeypatch.setattr(target_module, "window_list", lambda _: [{
        "kCGWindowNumber": 40, "kCGWindowOwnerName": "Test", "kCGWindowName": "Page",
        "kCGWindowBounds": {"Width": 600, "Height": 400}}])
    monkeypatch.setattr(target_module, "AXIsProcessTrustedWithOptions", lambda options: calls.append(options) or False)
    monkeypatch.setattr(target_module, "matching_ax_window", lambda *_: pytest.fail("no AX without permission"))
    result = target_module.describe_target(20)
    assert result["window_id"] == 40 and result["fingerprint"] == ""
    assert calls == [{"AXTrustedCheckOptionPrompt": False}]


def test_document_metadata_never_reads_text_values(monkeypatch):
    attrs = []
    def get_attr(el, name):
        attrs.append(name)
        return {("window", "AXChildren"): ["web"], ("web", "AXRole"): "AXWebArea",
                ("web", "AXURL"): "https://example.com"}.get((el, name))
    monkeypatch.setattr(target_module, "attribute", get_attr)
    assert target_module.document_id("window") == "https://example.com"
    assert "AXValue" not in attrs


def test_sharing_indicator_not_selected_as_document(monkeypatch):
    monkeypatch.setattr(target_module, "window_list", lambda _: [
        {"kCGWindowNumber": 1, "kCGWindowBounds": {"Width": 66, "Height": 20}},
        {"kCGWindowNumber": 2, "kCGWindowBounds": {"Width": 600, "Height": 400}},
        {"kCGWindowNumber": 3, "kCGWindowBounds": {"Width": 1200, "Height": 900}},
    ])
    monkeypatch.setattr(target_module, "AXIsProcessTrustedWithOptions", lambda _: False)
    assert target_module.describe_target(20)["window_id"] == 2  # Not the largest either.


def test_inline_original_bytes_preserved_without_network(monkeypatch):
    png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
           "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    monkeypatch.setattr(reader, "_secure_urlopen", lambda _: pytest.fail("inline image needs no network"))
    assert reader._download_as_data_url(png) == png
    assert reader._download_as_data_url("data:image/png;base64,broken") is None
