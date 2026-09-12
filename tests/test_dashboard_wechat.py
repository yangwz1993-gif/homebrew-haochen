"""Only public Dock badge metadata is modeled; no user messages are fixtures."""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
module = importlib.import_module("haochen_app.dashboard.adapters.wechat")
Store = importlib.import_module("haochen_app.dashboard.store").DashboardStore


@pytest.mark.parametrize("value", [None, "", " ", "微信", "1 unread", "-1", "100000", "•••"])
def test_absent_or_unrecognized_badge_is_not_zero(value):
    assert module.unread_badge(value) is None


@pytest.mark.parametrize("value,number,at_least", [("1", 1, False), ("99+", 99, True), ("0", 0, False)])
def test_explicit_badge_count(value, number, at_least):
    badge = module.unread_badge(value)
    assert badge["count"] == number and badge["atLeast"] is at_least


@pytest.mark.parametrize("state,status", [("not_running", "not_running"), ("not_installed", "not_running"),
    ("permission_required", "permission_required"), ("badge_unavailable", "partial"), ("dock_unavailable", "partial")])
def test_capability_limit_is_explicit(state, status):
    result = module.WeChatAdapter(lambda: {"state": state}).snapshot()
    assert result["status"] == status
    assert not result["events"]


def test_unknown_does_not_clear_last_known_count_as_zero(tmp_path):
    store = Store(tmp_path)
    store.enable("wechat", True)
    adapter = module.WeChatAdapter(lambda: {"state": "badge", "badge": module.unread_badge("2")})
    store.observe("wechat", adapter.snapshot())
    first = store.snapshot()["events"][0]
    assert first["unreadCount"] == 2
    assert first["target"]["kind"] == "wechat"
    store.observe("wechat", module.WeChatAdapter(lambda: {"state": "badge_unavailable"}).snapshot())
    retained = store.snapshot()["events"][0]
    assert retained["stale"] is True and retained["unreadCount"] == 2
    store.enable("wechat", False)
    assert not store.snapshot()["events"]


def test_explicit_zero_has_no_unread_event():
    result = module.WeChatAdapter(lambda: {"state": "badge", "badge": module.unread_badge("0")}).snapshot()
    assert result["status"] == "ready" and result["events"] == []


def test_errors_do_not_expose_raw_private_diagnostics():
    def fail():
        raise RuntimeError("SYNTHETIC_SECRET")
    result = module.WeChatAdapter(fail).snapshot()
    assert result["status"] == "partial" and "SYNTHETIC_SECRET" not in str(result)
