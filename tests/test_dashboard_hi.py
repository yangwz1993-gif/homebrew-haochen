"""Hi status monitor: honest, read-only aggregation of the user's own signals.

No real chat unread, no message bodies, no writes. All CLI output is faked; no
real Hi account data is a fixture.
"""

import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
module = importlib.import_module("haochen_app.dashboard.adapters.hi")
Store = importlib.import_module("haochen_app.dashboard.store").DashboardStore

IDENTITY = {"xhsContactId": "user@xiaohongshu.com"}


def _iso(delta_minutes):
    return (datetime.now().astimezone() + timedelta(minutes=delta_minutes)).isoformat(timespec="seconds")


def make_runner(responses):
    """Dispatch by the hi subcommand (first arg); values may be data or Exception."""

    def runner(_cli, *args):
        cmd = args[0]
        if cmd not in responses:
            if cmd == "search:message":
                return {"items": []}  # default: no follow-up messages
            if cmd == "search:employee":
                return {"items": []}  # default: no name tokens
            raise KeyError(cmd)
        value = responses[cmd]
        if isinstance(value, Exception):
            raise value
        return value

    return runner


def adapter(responses):
    return module.HiAdapter(cli_path="/fake/hi", runner=make_runner(responses))


def test_missing_cli_is_not_running(monkeypatch):
    monkeypatch.setattr(module, "_find_cli", lambda: None)
    result = module.HiAdapter(runner=make_runner({})).snapshot()
    assert result["status"] == "not_running"
    assert result["reasonCode"] == "cli_missing"
    assert not result["events"]


def test_auth_failure_is_permission_required():
    result = adapter({"search:me": module.HiError("cli_failed", "x")}).snapshot()
    assert result["status"] == "permission_required"
    assert result["reasonCode"] == "not_authenticated"
    assert not result["events"]


def test_empty_identity_is_permission_required():
    result = adapter({"search:me": {}}).snapshot()
    assert result["status"] == "permission_required"


def test_pending_tasks_are_not_surfaced_anymore():
    # 待我处理任务的推送已按所有者要求取消：即便有待办，也不产生任何事件。
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "todos:list-tasks": {"taskList": [
            {"taskStatus": "1", "title": "处理商户名单"},
            {"taskStatus": 1, "title": "跟进线上bug", "deadline": _iso(600)},
        ]},
    }
    result = adapter(responses).snapshot()
    assert result["status"] == "ready"
    assert not any(e["id"] == "hi:tasks:responsible" for e in result["events"])
    assert "任务" not in result["message"]


def test_only_at_me_messages_kept_others_dropped():
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "search:message": {"items": [
            {"messageId": "m1", "senderName": "同事A", "senderId": "a@xiaohongshu.com",
             "content": "闲聊今天午饭吃啥"},                                  # chatter -> dropped
            {"messageId": "m2", "senderName": "我自己", "senderId": "user@xiaohongshu.com",
             "content": "@张三 麻烦你看下"},                                  # sent by me -> dropped
            {"messageId": "m3", "senderName": "红书OA", "senderId": "oa@xiaohongshu.com",
             "content": "您提交的申请需要您审批"},                            # 审批(无@我) -> 现在丢弃，不打扰
            {"messageId": "m4", "senderName": "李四", "senderId": "l@xiaohongshu.com",
             "content": "@时田 帮忙 review 一下"},                            # @ 我 -> 保留
        ]},
    }
    result = adapter(responses).snapshot()
    ids = [e["id"] for e in result["events"] if e["id"].startswith("hi:msg:")]
    assert ids == ["hi:msg:m4"]                              # 只有 @我 的留下
    assert "hi:msg:m3" not in ids                            # 审批类不再打扰
    event = next(e for e in result["events"] if e["id"] == "hi:msg:m4")
    assert event["status"] == "needs_attention" and "@ 你" in event["title"]


def test_name_mention_without_at_symbol_is_caught():
    # Real Hi @-mentions render the name (no '@'); must still be surfaced.
    responses = {
        "search:me": IDENTITY,
        "search:employee": {"items": [{"redName": "时田", "userName": "杨文著"}]},
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "todos:list-tasks": {"taskList": []},
        "search:message": {"items": [
            {"messageId": "n1", "senderName": "童英", "senderId": "t@xiaohongshu.com",
             "content": "时田(杨文著)【加急】导出表格对应不上，看下"},   # names it, no '@'
            {"messageId": "n2", "senderName": "路人", "senderId": "p@xiaohongshu.com",
             "content": "今晚一起吃饭吗"},                              # unrelated chatter -> dropped
        ]},
    }
    result = adapter(responses).snapshot()
    ids = [e["id"] for e in result["events"] if e["id"].startswith("hi:msg:")]
    assert "hi:msg:n1" in ids and "hi:msg:n2" not in ids
    ev = next(e for e in result["events"] if e["id"] == "hi:msg:n1")
    assert ev["status"] == "needs_attention" and "@ 你" in ev["title"]


def test_ended_schedule_is_excluded_and_upcoming_kept():
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"hasDetailPermission": True, "scheduleList": [
            {"scheduleId": 1, "title": "已结束的会", "beginTime": _iso(-120), "endTime": _iso(-60)},
            {"scheduleId": 2, "title": "即将开始的会", "beginTime": _iso(60), "endTime": _iso(120)},
            {"scheduleId": 3, "title": "进行中的会", "beginTime": _iso(-30), "endTime": _iso(30)},
        ]}],
        "todos:list-tasks": {"taskList": []},
    }
    result = adapter(responses).snapshot()
    ids = {e["id"] for e in result["events"]}
    assert "hi:schedule:1" not in ids
    assert "hi:schedule:2" in ids and "hi:schedule:3" in ids
    # In-progress sorts before upcoming.
    schedule_ids = [e["id"] for e in result["events"] if e["id"].startswith("hi:schedule:")]
    assert schedule_ids[0] == "hi:schedule:3"
    assert all("unreadCount" not in e for e in result["events"] if e["id"].startswith("hi:schedule:"))


def test_no_detail_permission_hides_meeting_title():
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"hasDetailPermission": False, "scheduleList": [
            {"scheduleId": 9, "title": "机密评审", "beginTime": _iso(45), "endTime": _iso(90)},
        ]}],
        "todos:list-tasks": {"taskList": []},
    }
    result = adapter(responses).snapshot()
    event = next(e for e in result["events"] if e["id"] == "hi:schedule:9")
    assert "机密评审" not in event["summary"]
    assert event["title"] == "忙碌时段"


def test_subcall_failure_stays_ready_when_data_loaded():
    # A timeout on one call must NOT flip the whole connector to 能力受限/黄点 when
    # other signal loaded — that reads as broken. Stay "ready" with a soft note.
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": module.HiError("timeout", "x"),  # 一个子调用超时
        "search:message": {"items": [
            {"messageId": "z1", "senderName": "李四", "senderId": "l@xiaohongshu.com",
             "content": "@时田 看下"}]},                                  # @我 消息成功加载
    }
    result = adapter(responses).snapshot()
    assert result["status"] == "ready"
    assert any(e["id"].startswith("hi:msg:") for e in result["events"])
    assert "刷新" in result["message"]


def test_partial_only_when_nothing_loaded():
    responses = {
        "search:me": IDENTITY,
        "search:message": module.HiError("timeout", "x"),
        "calendar:get-user-schedules": module.HiError("timeout", "x"),
        "todos:list-tasks": module.HiError("timeout", "x"),
    }
    result = adapter(responses).snapshot()
    assert result["status"] == "partial"
    assert not result["events"]


def test_store_keeps_at_me_message(tmp_path):
    store = Store(tmp_path)
    store.enable("hi", True)
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "search:message": {"items": [
            {"messageId": "u1", "senderName": "李四", "senderId": "l@xiaohongshu.com",
             "content": "@时田 看下"}]},
    }
    store.observe("hi", adapter(responses).snapshot())
    event = store.snapshot()["events"][0]
    assert event["source"] == "hi"
    assert event["id"] == "hi:msg:u1"
