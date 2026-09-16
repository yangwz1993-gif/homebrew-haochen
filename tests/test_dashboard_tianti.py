"""天梯日报连接器 + 核心事实卡同步追踪的测试（全部走注入的假 runner/fetcher，不碰真实网络）。"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

tianti = importlib.import_module("haochen_app.dashboard.adapters.tianti")
keychain = importlib.import_module("haochen_app.keychain")
store_module = importlib.import_module("haochen_app.dashboard.store")

TiantiAdapter = tianti.TiantiAdapter
DashboardStore = store_module.DashboardStore

SAMPLE = {
    "schemaVersion": "2.0",
    "user": "yangwenzhu1@xiaohongshu.com",
    "projects": [
        {"id": "p_abc", "name": "开源阅读集成", "goal": "让用户读到全网内容",
         "desc": "阅读器项目", "stage": "交付运营期", "status": "active",
         "doc": "https://docs.example.com/x",
         "days": {"2026-09-16": {
             "progress": 80, "progressReason": "联调推进",
             "prog": {"points": [{"text": "联调：打通渲染管线", "by": "self"}], "rel": ""},
             "risk": {"level": "good", "read": "无卡点"},
             "todos": [{"tx": "补齐异常分支测试"}],
             "events": []}}},
        {"id": "p_risk", "name": "高危项目", "goal": "", "status": "active",
         "days": {"2026-09-16": {
             "progress": 30, "prog": {"points": [], "rel": ""}, "progressReason": "等依赖",
             "risk": {"level": "blocked", "read": "依赖方未交付"}, "todos": [], "events": []}}},
        {"id": "misc-xy", "name": "杂事", "status": "active",
         "days": {"2026-09-16": {"progress": 0, "prog": {"points": []},
                                 "risk": {"level": "good"}, "todos": [], "events": []}}},
    ],
}


def make_adapter(tmp_path, *, with_cookie=True, fetcher=None, runner=None):
    creds = keychain.MemoryCredentialStore()
    if with_cookie:
        creds.set("tianti", "test-cookie-value")
    return TiantiAdapter(tmp_path, keychain=creds,
                         runner=runner or (lambda cli, *a: {"xhsContactId": "yangwenzhu1@xiaohongshu.com"}),
                         fetcher=fetcher or (lambda email, cookie: SAMPLE)), creds


def test_snapshot_without_cookie_guides_one_click_connect(tmp_path):
    adapter, _ = make_adapter(tmp_path, with_cookie=False)
    result = adapter.snapshot()
    assert result["status"] == "permission_required"
    assert result["reasonCode"] == "cookie_missing"
    assert "一键连接" in result["message"] and "DevTools" not in result["message"]


def test_snapshot_expired_cookie_asks_reconnect(tmp_path):
    def expired(email, cookie):
        raise tianti.CookieExpired("401")
    adapter, _ = make_adapter(tmp_path, fetcher=expired)
    result = adapter.snapshot()
    assert result["status"] == "permission_required"
    assert result["reasonCode"] == "cookie_expired" and "重新授权" in result["message"]


def test_snapshot_normalizes_events_and_projects(tmp_path):
    adapter, _ = make_adapter(tmp_path)
    result = adapter.snapshot()
    assert result["status"] == "ready"
    assert result["email"] == "yangwenzhu1@xiaohongshu.com"
    # misc- 前缀的「其他」事项既不是动态也不进同步清单
    assert all(p["id"] != "misc-xy" for p in result["projects"])
    assert all(not e["id"].endswith("misc-xy") for e in result["events"])
    good = next(e for e in result["events"] if e["id"] == "tianti:p_abc")
    assert good["state"] == "idle"
    assert "联调：打通渲染管线" in good["summary"] and "进度 80%" in good["summary"]
    assert good["target"]["url"] == tianti.fact_card_url("p_abc")
    risk = next(e for e in result["events"] if e["id"] == "tianti:p_risk")
    assert risk["state"] == "needs_attention" and "依赖方未交付" in risk["summary"]
    # 缓存落盘（对话侧 read_daily 工具的事实源）
    cache = tmp_path / "dashboard" / "tianti_cache.json"
    assert cache.exists() and "开源阅读集成" in cache.read_text(encoding="utf-8")


def test_snapshot_hi_email_failure_is_partial_not_silent(tmp_path):
    def bad_runner(cli, *args):
        raise tianti.HiError("not_authenticated", "Hi 登录态失效")
    adapter, _ = make_adapter(tmp_path, runner=bad_runner)
    result = adapter.snapshot()
    assert result["status"] == "partial" and "身份邮箱" in result["message"]


# ── 核心事实卡 → 我在追踪（全自动同步）──────────────────────────


def test_sync_creates_tracks_with_fact_card_link(tmp_path):
    store = DashboardStore(tmp_path)
    adapter, _ = make_adapter(tmp_path)
    projects = adapter.snapshot()["projects"]
    created, updated = store.sync_tianti_tracks(projects)
    assert created == 2 and updated == 0  # misc- 与无风险项都在；两个正式项目各一条
    tracks = store.snapshot()["tracks"]
    reading = next(t for t in tracks if t["title"] == "开源阅读集成")
    assert reading["goal"] == "让用户读到全网内容"
    assert reading["frequency"] == "daily"
    source = reading["sources"][0]
    assert source["type"] == "url" and source["tiantiPid"] == "p_abc"
    assert source["locator"] == tianti.fact_card_url("p_abc")
    assert "核心事实卡" in source["label"]


def test_sync_is_idempotent_and_updates_goal(tmp_path):
    store = DashboardStore(tmp_path)
    adapter, _ = make_adapter(tmp_path)
    store.sync_tianti_tracks(adapter.snapshot()["projects"])
    again, updated = store.sync_tianti_tracks(adapter.snapshot()["projects"])
    assert again == 0 and updated == 0  # 第二次同步不产生重复事项
    projects = adapter.snapshot()["projects"]
    for p in projects:
        if p["id"] == "p_abc":
            p["goal"] = "新目标"
    created, updated = store.sync_tianti_tracks(projects)
    assert created == 0 and updated == 1
    track = next(t for t in store.snapshot()["tracks"] if t["title"] == "开源阅读集成")
    assert track["goal"] == "新目标"


def test_sync_never_resurrects_user_deleted_track(tmp_path):
    store = DashboardStore(tmp_path)
    adapter, _ = make_adapter(tmp_path)
    store.sync_tianti_tracks(adapter.snapshot()["projects"])
    track = next(t for t in store.snapshot()["tracks"] if t["title"] == "开源阅读集成")
    store.track_delete(track["id"])
    created, _ = store.sync_tianti_tracks(adapter.snapshot()["projects"])
    assert created == 0
    assert not any(t["title"] == "开源阅读集成" for t in store.snapshot()["tracks"])
