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


def make_runner(responses, calls=None):
    """Dispatch by the hi subcommand (first arg); values may be data or Exception.
    定向拉取（--chat-ids <id>）按 "chat:<id>" 键取 fixture；calls 传入列表可记录调用。"""

    def runner(_cli, *args):
        if calls is not None:
            calls.append(args)
        cmd = args[0]
        if cmd == "search:message" and "--chat-ids" in args:
            cid = args[args.index("--chat-ids") + 1]
            value = responses.get(f"chat:{cid}", {"items": []})
            if isinstance(value, Exception):
                raise value
            return value
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


def adapter(responses, calls=None):
    return module.HiAdapter(cli_path="/fake/hi", runner=make_runner(responses, calls))


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
    # 行为变更（0.6.2-beta.3）：partial 且无任何内容时也给明确占位（带降级说明），
    # 不再让动态区空白——这是用户报告「空占位还是没有」的修复。
    responses = {
        "search:me": IDENTITY,
        "search:message": module.HiError("timeout", "x"),
        "calendar:get-user-schedules": module.HiError("timeout", "x"),
        "todos:list-tasks": module.HiError("timeout", "x"),
    }
    result = adapter(responses).snapshot()
    assert result["status"] == "partial"
    placeholder = [e for e in result["events"] if e["id"] == "hi:none"]
    assert placeholder, "partial 且空内容时必须有占位卡片"
    assert "没有 @ 你" in placeholder[0]["summary"]
    assert any("稍后自动刷新" in ev.get("text", "") for ev in placeholder[0]["evidence"])


def test_placeholder_when_ready_and_empty():
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "search:message": {"items": []},
    }
    result = adapter(responses).snapshot()
    assert result["status"] == "ready"
    placeholder = [e for e in result["events"] if e["id"] == "hi:none"]
    assert placeholder and "没有 @ 你" in placeholder[0]["summary"]
    assert not placeholder[0]["evidence"]  # ready 态不带降级说明


def test_ready_with_real_events_has_no_placeholder():
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "search:message": {"items": [
            {"messageId": "z1", "senderName": "李四", "senderId": "l@xiaohongshu.com",
             "content": "@时田 看下"}]},
    }
    result = adapter(responses).snapshot()
    assert result["status"] == "ready"
    assert any(e["id"].startswith("hi:msg:") for e in result["events"])
    assert not any(e["id"] == "hi:none" for e in result["events"])


def test_placeholder_never_fakes_unread(tmp_path):
    """诚实性：占位卡片不算未读、不进 activity 计数、不参与「有新结果」。"""
    from haochen_app.dashboard.attention import activity
    store = Store(tmp_path)
    store.enable("hi", True)
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "search:message": {"items": []},
    }
    store.observe("hi", adapter(responses).snapshot())
    events = store.snapshot()["events"]
    assert len(events) == 1 and events[0]["id"] == "hi:none"
    assert events[0]["reasonCode"] == "empty" and events[0]["status"] == "available"
    act = activity(events, [{"id": "hi", "enabled": True, "status": "connected"}])
    assert act["kind"] == "idle" and act["count"] == 0


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


def _msg(mid, sender, sender_id, content, minutes_ago, chat):
    ts = (datetime.now().astimezone() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    return {"messageId": mid, "senderName": sender, "senderId": sender_id,
            "content": content, "sendTime": ts, "chatId": chat}


def test_unreplied_private_chat_detected():
    """未回私聊（两步法）：定向验证为两人会话且最后一条是对方发的才报；
    已回/群聊（定向拉出人多于 2）/机器人都不报。"""
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "search:message": {"items": [
            _msg("m1", "赵咪", "zhaomi@xiaohongshu.net", "帮我看下", 60, "CHAT_A"),
            _msg("m2", "对方", "other@xiaohongshu.net", "在吗", 50, "CHAT_B"),
            _msg("m3", "我", "user@xiaohongshu.com", "在的", 40, "CHAT_B"),
            _msg("m4", "甲", "a@xiaohongshu.net", "x", 30, "CHAT_C"),
            _msg("m7", "考勤排班", "xxx@bot.com", "提醒", 10, "CHAT_D"),
        ]},
        # 第二步定向验证的全量消息流
        "chat:CHAT_A": {"items": [
            _msg("m1", "赵咪", "zhaomi@xiaohongshu.net", "帮我看下", 60, "CHAT_A"),
        ]},
        "chat:CHAT_C": {"items": [  # 定向拉出 3 个真人 → 群聊，别误判
            _msg("g1", "甲", "a@xiaohongshu.net", "x", 30, "CHAT_C"),
            _msg("g2", "乙", "b@xiaohongshu.net", "y", 25, "CHAT_C"),
            _msg("g3", "丙", "c@xiaohongshu.net", "z", 20, "CHAT_C"),
        ]},
        "chat:CHAT_D": {"items": [  # 纯机器人 → 排除
            _msg("b1", "考勤排班", "xxx@bot.com", "提醒", 10, "CHAT_D"),
        ]},
    }
    result = adapter(responses).snapshot()
    unreplied = [e for e in result["events"] if e.get("reasonCode") == "unreplied_private"]
    assert [e["chatId"] for e in unreplied] == ["CHAT_A"]
    assert "未回私聊" in unreplied[0]["title"] and "赵咪" in unreplied[0]["title"]
    assert unreplied[0]["status"] == "needs_attention"
    assert any("两步法" in ev.get("text", "") for ev in unreplied[0]["evidence"])


def test_unreplied_empty_when_i_replied_last():
    """粗筛层就拦掉（我最后发言 → 已回），不再做定向验证。"""
    calls = []
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "search:message": {"items": [
            _msg("m1", "赵咪", "zhaomi@xiaohongshu.net", "看下", 60, "CHAT_A"),
            _msg("m2", "我", "user@xiaohongshu.com", "看了", 30, "CHAT_A"),
        ]},
    }
    result = adapter(responses, calls=calls).snapshot()
    assert not [e for e in result["events"] if e.get("reasonCode") == "unreplied_private"]
    # 粗筛生效：没有发生任何定向验证调用
    assert not any("--chat-ids" in args for args in calls)


def test_unreplied_handles_naive_timestamps():
    """回归：hi 返回的无时区时间戳不得导致比较崩溃（实测发现）。"""
    naive = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")  # 无时区
    responses = {
        "search:me": IDENTITY,
        "calendar:get-user-schedules": [{"scheduleList": [], "hasDetailPermission": True}],
        "search:message": {"items": [
            {"messageId": "m1", "senderName": "赵咪", "senderId": "zhaomi@xiaohongshu.net",
             "content": "看下", "sendTime": naive, "chatId": "CHAT_A"},
        ]},
        "chat:CHAT_A": {"items": [
            {"messageId": "m1", "senderName": "赵咪", "senderId": "zhaomi@xiaohongshu.net",
             "content": "看下", "sendTime": naive, "chatId": "CHAT_A"},
        ]},
    }
    result = adapter(responses).snapshot()
    assert [e["chatId"] for e in result["events"] if e.get("reasonCode") == "unreplied_private"] == ["CHAT_A"]
