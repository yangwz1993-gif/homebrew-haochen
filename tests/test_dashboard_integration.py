"""Cross-boundary dashboard regressions, using only isolated synthetic data."""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
store_module = importlib.import_module("haochen_app.dashboard.store")
browser_module = importlib.import_module("haochen_app.dashboard.adapters.browser")
host_module = importlib.import_module("haochen_app.dashboard.browser_host")
controller = importlib.import_module("haochen_app.dashboard.controller")


def observation(content="第一版资料"):
    return {"sourceId": "qa-source-0001", "url": "https://example.com/qa", "title": "QA 合成网页",
            "content": content, "status": "available", "coverage": "main_frame_text", "tabId": 3, "windowId": 2}


def test_browser_content_change_is_a_real_dashboard_event(tmp_path):
    native = host_module.BrowserStore(tmp_path)
    session = native.connect("qa-client-0001")
    adapter = browser_module.BrowserAdapter(tmp_path)
    store = store_module.DashboardStore(tmp_path)
    native.observe(observation(), session, "qa-client-0001")
    store.observe("browser", adapter.snapshot())
    assert store.snapshot()["history"] == []
    native.observe(observation("第二版资料：实际进展改变"), session, "qa-client-0001", now=time.time() + .05)
    store.observe("browser", adapter.snapshot())
    assert len(store.snapshot()["history"]) == 1
    assert "第二版" in store.snapshot()["events"][0]["summary"]
    assert isinstance(store.snapshot()["events"][0]["evidence"], list)
    native.close()


def test_disabled_browser_rejects_already_connected_native_host(tmp_path):
    native = host_module.BrowserStore(tmp_path)
    session = native.connect("qa-client-0001")
    native.observe(observation(), session, "qa-client-0001")
    adapter = browser_module.BrowserAdapter(tmp_path)
    adapter.set_enabled(False)
    assert adapter.evidence("qa-source-0001")["content"] == ""
    with pytest.raises(ValueError, match="disabled"):
        native.observe(observation("不应保存"), session, "qa-client-0001")
    assert native.records() == []
    adapter.set_enabled(True)
    with pytest.raises(ValueError, match="revoked"):
        native.observe(observation("旧会话不能恢复"), session, "qa-client-0001")
    session = native.connect("qa-client-0001")
    native.observe(observation("重新开启后新资料"), session, "qa-client-0001")
    assert "新资料" in adapter.evidence("qa-source-0001")["content"]
    native.close()


@pytest.mark.parametrize("message", [None, [], {}, {"v": 1, "id": "qa", "action": "bash", "payload": {}},
                                    {"v": 1, "id": "qa", "action": "refresh", "payload": []}])
def test_native_boundary_rejects_unknown_or_malformed_actions(message):
    with pytest.raises(ValueError):
        controller.validate_message(message)


def test_all_four_appearance_choices_are_persisted(tmp_path):
    store = store_module.DashboardStore(tmp_path)
    for palette in ("sage", "stone", "mist", "carbon"):
        store.settings_update({"palette": palette})
        assert store_module.DashboardStore(tmp_path).snapshot()["settings"]["palette"] == palette


def test_old_collector_cannot_revive_after_disable_and_reenable(tmp_path):
    store = store_module.DashboardStore(tmp_path)
    revision = store.source_revision("otty")
    store.enable("otty", False)
    store.enable("otty", True)
    store.observe("otty", {"status": "ready", "events": [{"id": "otty:qa", "title": "过时数据"}]}, revision=revision)
    assert store.snapshot()["events"] == []
