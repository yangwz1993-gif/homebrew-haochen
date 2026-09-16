"""Content-versioned attention, tested without applications or user data."""

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
attention = importlib.import_module("haochen_app.dashboard.attention")
Store = importlib.import_module("haochen_app.dashboard.store").DashboardStore


def event(identifier="test", **extra):
    return {"id": identifier, "title": "Synthetic task", "state": "idle", "summary": "Synthetic result", **extra}


def observe(store, *events, source="browser"):
    # 通用未读机制用 browser 当载体；otty 会话卡是 alertVersion 语义，见专属测试
    store.observe(source, {"status": "ready", "events": list(events)})
    return store.snapshot()["events"]


def test_baseline_is_quiet_and_new_results_persist_unread_across_restart(tmp_path):
    store = Store(tmp_path)
    assert observe(store, event())[0]["unread"] is False
    rows = observe(store, event(), event("new"))
    assert rows[1]["unread"] is True
    reloaded = Store(tmp_path)
    assert reloaded.snapshot()["events"][1]["unread"] is True


def test_initial_waiting_state_is_actionable_and_ack_is_persisted(tmp_path):
    store = Store(tmp_path)
    row = observe(store, event(state="awaiting"))[0]
    assert row["unread"] is True
    assert attention.activity([row], [])["kind"] == "attention"
    assert store.mark_read(row["id"], row["attentionVersion"]) == {"acknowledged": True}
    assert Store(tmp_path).snapshot()["events"][0]["unread"] is False


def test_read_ack_cannot_clear_a_newer_result(tmp_path):
    store = Store(tmp_path)
    old = observe(store, event())[0]
    fresh = observe(store, event(summary="A newer synthetic result"))[0]
    assert fresh["unread"] is True
    assert store.mark_read(old["id"], old["attentionVersion"])["acknowledged"] is False
    assert store.snapshot()["events"][0]["unread"] is True
    assert store.mark_read(fresh["id"], fresh["attentionVersion"])["acknowledged"] is True


def test_poll_time_and_staleness_do_not_create_new_attention(tmp_path):
    store = Store(tmp_path)
    first = observe(store, event(updatedAt="2026-01-01T00:00:00Z"))[0]
    second = observe(store, event(updatedAt="2026-01-01T00:00:20Z"))[0]
    assert first["attentionVersion"] == second["attentionVersion"]
    assert second["unread"] is False
    assert attention.version(first) == attention.version({**first, "stale": True, "incomplete": True})


def test_disconnecting_removes_read_metadata(tmp_path):
    store = Store(tmp_path)
    row = observe(store, event(), source="otty")[0]
    assert row["id"] in store.data["readVersions"]
    store.enable("otty", False)
    assert store.data["readVersions"] == {}
    with pytest.raises(ValueError):
        store.mark_read(row["id"], row["attentionVersion"])


def test_legacy_side_default_migrates_once_and_respects_new_choice(tmp_path):
    store = Store(tmp_path)
    observe(store, event())
    data = json.loads(store.path.read_text())
    data.pop("entranceRevision")
    data.pop("readVersions")
    data["settings"]["dock"] = "side"
    store.path.write_text(json.dumps(data))
    reloaded = Store(tmp_path)
    assert reloaded.snapshot()["settings"]["dock"] == "notch"
    assert reloaded.snapshot()["events"][0]["unread"] is False
    reloaded.settings_update({"dock": "pet"})
    assert Store(tmp_path).snapshot()["settings"]["dock"] == "pet"


@pytest.mark.parametrize("states,expected", [
    (["processing", "idle", "error", "awaiting"], "attention"),
    (["processing", "idle", "error"], "error"),
    (["processing", "idle"], "new"),
    (["processing"], "running"),
    (["unknown"], "idle"),
])
def test_priority(states, expected):
    rows = [{"status": state, "unread": True} for state in states]
    assert attention.activity(rows, [])["kind"] == expected


def test_stale_events_never_claim_current_work_or_new_results():
    assert attention.activity([{"status": "processing", "stale": True, "unread": True}], [])["kind"] == "idle"


def test_connector_permission_problem_stays_on_card_not_notch():
    """行为变更（0.6.2-beta.3）：连接权限问题改由连接卡片持续展示，刘海不再常驻提醒；
    且任何地方都不得泄露摘要内容。"""
    state = attention.activity([], [{"enabled": True, "status": "permission_required", "summary": "SECRET"}])
    assert state["kind"] == "idle"
    assert "SECRET" not in json.dumps(state)


def test_counter_is_bounded():
    assert attention.activity([{"status": "idle", "unread": True}] * 1200, [])["count"] == 999


@pytest.mark.parametrize("status", ["tab_closed", "target_changed", "suspended", "stale", "disconnected"])
def test_unavailable_browser_target_is_not_a_new_result(status):
    state = attention.activity([{"status": status, "unread": True}], [])
    assert state["kind"] == "error"


def test_browser_reading_is_working_not_a_result():
    assert attention.activity([{"status": "reading", "unread": True}], [])["kind"] == "running"


def test_lost_connector_does_not_nag_at_notch():
    """行为变更（0.6.2-beta.3）：连接中断不再在刘海常驻提醒（警报疲劳）；
    它持续显示在连接卡片上。未配置的连接器保持安静。"""
    connector = {"enabled": True, "status": "unavailable", "hasConnected": False}
    assert attention.activity([], [connector])["kind"] == "idle"
    connector["hasConnected"] = True
    assert attention.activity([], [connector])["kind"] == "idle"


@pytest.mark.parametrize("status", ["awaiting", "error"])
def test_read_means_no_nagging(status):
    """行为变更（0.6.2-beta.3）：已读 = 不再提醒。旧语义「已读未解决仍提醒」会让
    刘海在零待办时仍挂「等你确认」（误报）；现在已读项不再驱动提醒。"""
    value = attention.activity([{"status": status, "unread": False}], [])
    assert value["kind"] == "idle" and value["count"] == 0


def test_attention_label_names_the_source_app():
    """提醒文案必须指名是哪个应用（用户在刘海就要知道去哪处理）。"""
    state = attention.activity(
        [{"status": "needs_attention", "unread": True, "source": "hi"}], [])
    assert state["kind"] == "attention" and "Hi" in state["label"]
    state = attention.activity(
        [{"status": "awaiting", "unread": True, "source": "otty"}], [])
    assert "Otty" in state["label"]
    state = attention.activity(
        [{"status": "awaiting", "unread": True, "source": "hi"},
         {"status": "needs_attention", "unread": True, "source": "calendar"}], [])
    assert "Hi" in state["label"] and "日历" in state["label"]


def test_failed_read_save_rolls_back_and_can_retry(tmp_path, monkeypatch):
    store = Store(tmp_path)
    original = observe(store, event())[0]
    row = observe(store, event(summary="New version"))[0]
    save = store._save

    def fail():
        raise OSError("Synthetic disk failure")

    monkeypatch.setattr(store, "_save", fail)
    with pytest.raises(OSError):
        store.mark_read(row["id"], row["attentionVersion"])
    assert store.data["readVersions"][row["id"]] == original["attentionVersion"]
    assert store.snapshot()["events"][0]["unread"] is True
    monkeypatch.setattr(store, "_save", save)
    assert store.mark_read(row["id"], row["attentionVersion"])["acknowledged"] is True


def test_calendar_duration_change_has_its_own_unread_version_and_history(tmp_path):
    store = Store(tmp_path)
    store.enable("calendar", True)
    original = event(state="upcoming", startAt="2026-09-13T10:00:00+08:00", endAt="2026-09-13T11:00:00+08:00")
    store.observe("calendar", {"status": "ready", "events": [original]})
    previous = store.snapshot()["events"][0]
    store.observe("calendar", {"status": "ready", "events": [{**original, "endAt": "2026-09-13T11:30:00+08:00"}]})
    current = store.snapshot()["events"][0]
    assert current["unread"] is True and current["attentionVersion"] != previous["attentionVersion"]
    assert store.mark_read(previous["id"], previous["attentionVersion"])["acknowledged"] is False
    assert store.snapshot()["history"][-1]["changeType"] == "changed"


def test_unread_error_event_still_shows_at_notch():
    """保留语义：未读的错误类动态仍在刘海提醒（连接卡片和事件是两个层面）。"""
    state = attention.activity([{"status": "error", "unread": True, "source": "browser"}], [])
    assert state["kind"] == "error" and state["count"] == 1


def test_new_results_label_names_source_app():
    """「有新结果」也要指名哪个应用。"""
    state = attention.activity([{"status": "available", "unread": True, "source": "browser"}], [])
    assert state["kind"] == "new" and "网页" in state["label"]


def test_otty_idle_status_card_is_not_a_new_result():
    """Otty 常态实时状态卡（无 kind）不算新结果；turn_finished 等通知类（有 kind）才算。"""
    state = attention.activity([{"status": "idle", "unread": True, "source": "otty"}], [])
    assert state["kind"] == "idle"
    state = attention.activity(
        [{"status": "idle", "unread": True, "source": "otty", "kind": "turn_finished"}], [])
    assert state["kind"] == "new" and "Otty" in state["label"]


def change_event(pane="p_1", summary="本轮处理已结束；事项是否完成仍由你确认。"):
    """Otty 适配器在「一轮跑完」跃迁当次返回的 turn_finished 变化事件。"""
    return {"id": f"otty:{pane}:turn_finished:20260916183200", "kind": "turn_finished",
            "title": "π - 测试会话", "state": "idle", "summary": summary,
            "previousState": "processing", "updatedAt": "2026-09-16T10:32:00+00:00",
            "target": {"kind": "otty", "paneId": pane}}


def observe_with_changes(store, events, changes):
    store.observe("otty", {"status": "ready", "events": events, "changes": changes})
    return store.snapshot()["events"]


def test_turn_finished_lights_up_its_session_card_and_names_otty(tmp_path):
    """一轮跑完 → 所属会话卡被点亮（未读+摘要换「本轮处理已结束」），刘海指名「Otty」。"""
    store = Store(tmp_path)
    observe(store, event("otty:p_1"), source="otty")
    rows = observe_with_changes(store, [event("otty:p_1")], [change_event("p_1")])
    assert len(rows) == 1  # 不生成独立变化卡
    card = rows[0]
    assert card["unread"] is True and card.get("alertVersion")
    assert "本轮处理已结束" in card["summary"]
    activity = attention.activity(rows, [])
    assert activity["kind"] == "new" and "Otty" in activity["label"]


def test_state_changed_does_not_light_up(tmp_path):
    """开始干活（state_changed）不点亮：跑了不算消息，跑完才算。"""
    store = Store(tmp_path)
    observe(store, event("otty:p_1"), source="otty")
    started = change_event("p_1")
    started.update(kind="state_changed", previousState="idle")
    started["id"] = started["id"].replace("turn_finished", "state_changed")
    rows = observe_with_changes(store, [event("otty:p_1", state="processing")], [started])
    assert rows[0]["unread"] is False and not rows[0].get("alertVersion")


def test_lit_card_stays_read_across_state_flips(tmp_path):
    """已读后「开始干活/回到空闲」等翻转不复燃未读——未读只认 alertVersion。"""
    store = Store(tmp_path)
    observe(store, event("otty:p_1"), source="otty")
    rows = observe_with_changes(store, [event("otty:p_1")], [change_event("p_1")])
    card = rows[0]
    assert store.mark_read(card["id"], card["attentionVersion"])["acknowledged"] is True
    rows = observe(store, event("otty:p_1", state="processing", summary="正在处理"), source="otty")
    assert rows[0]["unread"] is False
    rows = observe(store, event("otty:p_1"), source="otty")
    assert rows[0]["unread"] is False


def test_next_turn_finished_relights_the_card(tmp_path):
    """读完后再跑完一轮 → 重新点亮（alertVersion 更新）。"""
    store = Store(tmp_path)
    observe(store, event("otty:p_1"), source="otty")
    rows = observe_with_changes(store, [event("otty:p_1")], [change_event("p_1")])
    store.mark_read(rows[0]["id"], rows[0]["attentionVersion"])
    rows = observe_with_changes(store, [event("otty:p_1")],
                                [change_event("p_1", summary="本轮处理已结束（第二轮）")])
    assert rows[0]["unread"] is True
    assert "第二轮" in rows[0]["summary"]


def test_legacy_one_shot_cards_are_swept(tmp_path):
    """迁移：beta.3 早期单独成卡的变化事件（同名混淆+刷屏）被清出列表。"""
    store = Store(tmp_path)
    observe(store, event("otty:p_1"), source="otty")
    legacy = {"id": "otty:p_1:turn_finished:old", "kind": "turn_finished", "source": "otty",
              "title": "π - 测试会话", "state": "idle", "summary": "旧卡",
              "updatedAt": "2026-09-16T10:00:00+00:00"}
    store.data["events"].append(legacy)
    store._save()
    rows = observe(store, event("otty:p_1"), source="otty")
    assert [r["id"] for r in rows] == ["otty:p_1"]


def test_lit_card_survives_restart_unread(tmp_path):
    """点亮状态持久化：重启 App 后未读仍在。"""
    store = Store(tmp_path)
    observe(store, event("otty:p_1"), source="otty")
    observe_with_changes(store, [event("otty:p_1")], [change_event("p_1")])
    reloaded = Store(tmp_path)
    rows = reloaded.snapshot()["events"]
    assert rows[0]["unread"] is True and rows[0].get("alertVersion")
    # 重启后第一轮轮询（基线丢失）也不得把它静默成已读
    store2 = Store(tmp_path)
    rows = observe(store2, event("otty:p_1"), source="otty")
    assert rows[0]["unread"] is True


def test_otty_new_result_label_names_the_session(tmp_path):
    """「有新结果」太含蓄：Otty 跑完一轮的提醒要指名到具体会话。"""
    store = Store(tmp_path)
    observe(store, event("otty:p_1", title="π - 开源阅读集成 - yangwenzhu1"), source="otty")
    rows = observe_with_changes(
        store, [event("otty:p_1", title="π - 开源阅读集成 - yangwenzhu1")], [change_event("p_1")])
    label = attention.activity(rows, [])["label"]
    assert label == "Otty · 开源阅读集成 跑完了", label


def test_otty_new_result_label_counts_multiple_sessions(tmp_path):
    store = Store(tmp_path)
    cards = [event("otty:p_1", title="π - 甲 - u"), event("otty:p_2", title="π - 乙 - u")]
    observe(store, *cards, source="otty")
    rows = observe_with_changes(store, cards, [change_event("p_1"), change_event("p_2")])
    label = attention.activity(rows, [])["label"]
    assert label == "Otty 有 2 个会话跑完了", label


def test_otty_new_result_label_falls_back_on_messy_title(tmp_path):
    store = Store(tmp_path)
    observe(store, event("otty:p_1", title=""), source="otty")
    rows = observe_with_changes(store, [event("otty:p_1", title="")], [change_event("p_1")])
    label = attention.activity(rows, [])["label"]
    assert label == "Otty · 有会话 跑完了", label
