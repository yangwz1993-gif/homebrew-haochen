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
    monkeypatch.setattr(reader, "web_area", lambda _: None)
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


def test_spa_page_url_takes_priority_over_stale_window_document(monkeypatch):
    attrs = []
    def get_attr(el, name):
        attrs.append(name)
        return {("window", "AXDocument"): "https://www.bbc.com/",
                ("window", "AXChildren"): ["page"], ("page", "AXRole"): "AXWebArea",
                ("page", "AXURL"): "https://www.bbc.com/news/articles/current"}.get((el, name))
    monkeypatch.setattr(target_module, "attribute", get_attr)
    assert target_module.document_id("window") == "https://www.bbc.com/news/articles/current"
    assert "AXValue" not in attrs


def test_duplicate_images_fetched_once_without_losing_blocks(monkeypatch):
    urls = []
    monkeypatch.setattr(reader, "_download_as_data_url", lambda url: urls.append(url) or "original bytes")
    content = reader.WindowContent(blocks=[reader.Block("image", url="https://example.com/a.png") for _ in range(15)])
    assert reader.prepare_images(content) == dict.fromkeys(range(15), "original bytes")
    assert urls == ["https://example.com/a.png"]


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


def test_retained_window_does_not_reresolve_after_consent(monkeypatch):
    setup_window(monkeypatch)
    bound = reader.BoundTarget(target())
    monkeypatch.setattr(reader, "matching_ax_window", lambda *_: pytest.fail("must use retained object"))
    assert reader.read_bound_snapshot(target(), bound)[0].blocks


def test_retained_browser_page_survives_tab_switch_without_new_tab_screenshot(monkeypatch):
    setup_window(monkeypatch, [reader.Block("image", url="https://example.com/original.png")])
    monkeypatch.setattr(reader, "web_area", lambda _: "page-a")
    monkeypatch.setattr(reader, "_copy", lambda el, name: {
        ("page-a", "AXURL"): "https://example.com/a", ("page-a", "AXRole"): "AXWebArea",
    }.get((el, name)))
    bound = reader.BoundTarget(target())
    monkeypatch.setattr(reader, "fingerprint", lambda _: "page-b")
    monkeypatch.setattr(reader, "capture_window_image", lambda *_a, **_k: pytest.fail("would capture new tab"))
    content, shot, _ = reader.read_bound_snapshot(target(), bound)
    assert content.blocks[0].url.endswith("original.png") and shot is None


def test_file_binding_does_not_read_bytes_until_authorized(monkeypatch, tmp_path):
    setup_window(monkeypatch)
    path = tmp_path / "own-fixture.txt"
    path.write_text("saved document A", encoding="utf8")
    t = {**target(), "document_uri": path.as_uri()}
    monkeypatch.setattr(reader, "matching_ax_window", lambda *_: None)
    original = Path.open
    monkeypatch.setattr(Path, "open", lambda *_a, **_k: pytest.fail("no bytes before consent"))
    bound = reader.BoundTarget(t)
    monkeypatch.setattr(Path, "open", original)
    content, shot, _ = reader.read_bound_snapshot(t, bound)
    assert content.blocks[0].text == "saved document A" and shot is None
    assert "不包含未保存" in content.scope
    path.write_text("changed version", encoding="utf8")
    with pytest.raises(ValueError, match="文件已变化"):
        reader.read_bound_snapshot(t, bound)


def test_resolve_background_windows_uses_all_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(target_module, "CGWindowListCopyWindowInfo", lambda options, _: calls.append(options) or [])
    assert target_module.matching_ax_window(20, 40) is None
    assert calls == [target_module.kCGWindowListOptionAll | target_module.kCGWindowListExcludeDesktopElements]


def test_browser_heading_uses_text_not_numeric_level(monkeypatch):
    monkeypatch.setattr(reader, "_copy", lambda el, name: {
        ("heading", "AXRole"): "AXHeading", ("heading", "AXValue"): 1,
        ("heading", "AXChildren"): ["text"], ("text", "AXRole"): "AXStaticText",
        ("text", "AXValue"): "Real heading",
    }.get((el, name)))
    monkeypatch.setattr(reader, "_geom", lambda _: (0, 0))
    blocks = []
    reader._walk("heading", 0, blocks)
    assert [(b.text, b.role) for b in blocks] == [("Real heading", "AXHeading")]


def test_local_image_uses_original_bytes_not_preview(monkeypatch, tmp_path):
    import base64
    setup_window(monkeypatch)
    path = tmp_path / "original.png"
    encoded = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
               "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    path.write_bytes(base64.b64decode(encoded))
    t = {**target(), "document_uri": path.as_uri()}
    monkeypatch.setattr(reader, "document_id", lambda _: path.as_uri())
    monkeypatch.setattr(reader, "_walk", lambda *_: pytest.fail("original image needs no AX body"))
    content, shot, _ = reader.read_bound_snapshot(t)
    assert content.blocks[0].url == "data:image/png;base64," + encoded
    assert shot is None and "已保存" in content.scope


def test_pdf_saved_text_layer_uses_native_parser(tmp_path):
    from reader.document_source import DocumentSource
    stream = b"BT /F1 12 Tf 20 100 Td (Synthetic saved PDF) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
               b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data, offsets = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    data += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    path = tmp_path / "owned-fixture.pdf"
    path.write_bytes(data)
    kind, text = DocumentSource(path.as_uri()).read()
    assert kind == "text" and "Synthetic saved PDF" in text
