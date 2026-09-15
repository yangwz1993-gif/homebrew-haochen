"""Private, versioned dashboard state and bounded observation history.

Only references explicitly selected by the user are tracked. Removing a shelf
entry or a track never deletes the underlying file, calendar item or app data.
"""

from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from ..secure_storage import atomic_write_private, ensure_private_directory, ensure_private_file
from .attention import ERRORS, WAITING, decorate, version

FREQUENCIES = {"manual": 0, "quarter": 900, "hourly": 3600, "daily": 86400}
PALETTES = {"glass", "sage", "stone", "mist", "carbon"}
MAX_STATE_BYTES = 32 * 1024 * 1024


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_text(value, limit=240) -> str:
    return str(value or "").strip()[:limit]


def evidence_preview(evidence, limit=2000):
    result = copy.deepcopy(evidence)
    for item in result if isinstance(result, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", ""))
        if len(text) > limit:
            item["text"] = text[:limit] + "\n[仅展示证据摘录；完整内容请打开来源]"
            item["previewTruncated"] = True
    return result


class DashboardStore:
    def __init__(self, home: Path):
        self.path = ensure_private_directory(Path(home) / "dashboard") / "state.json"
        self.lock = threading.RLock()
        self._source_versions = {}
        # A baseline belongs to one uninterrupted in-process connection, not
        # persisted state: reopening the app cannot observe offline activity.
        self._observation_baselines = {}
        self._last_save = 0
        self._revoked_sources = set()
        self.data = {
            "schema": 1, "events": [], "history": [], "tracks": [], "files": [], "reports": [],
            "readVersions": {}, "entranceRevision": 5,
            "settings": {"palette": "glass", "motion": "system", "dock": "notch", "aiDaily": False,
                         "connectors": {"otty": True, "browser": True, "calendar": False, "wechat": False,
                                        "hi": True}},
        }
        migrate_reads = False
        if self.path.exists():
            ensure_private_file(self.path)
            if self.path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError("总览数据超过安全读取上限，原文件未改变")
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or loaded.get("schema") != 1:
                raise ValueError("总览数据格式无法识别，原文件未改变")
            for key in ("events", "history", "tracks", "files", "reports"):
                if not isinstance(loaded.get(key), list):
                    raise ValueError("总览数据不完整，原文件未改变")
            migrate_reads = "readVersions" not in loaded
            self.data.update(loaded)
            # beta.1 defaulted to a side button; migrate that initial UI once.
            # Subsequent explicit choices (including pet-only) remain intact.
            rev = loaded.get("entranceRevision", 0)
            if rev < 2:
                self.data["settings"]["dock"] = "notch"
            # 0.6.2 glass redesign: adopt the glass theme once. After this runs the
            # rev is 4, so a later explicit palette choice sticks (never re-forced).
            if rev < 4:
                self.data["settings"]["palette"] = "glass"
            # 0.6.2: Hi is on by default now (hi CLI is already logged in);
            # enable it once for existing profiles too.
            if rev < 5:
                self.data["settings"]["connectors"]["hi"] = True
                self.data["entranceRevision"] = 5
        if not isinstance(self.data.get("readVersions"), dict):
            self.data["readVersions"] = {}
        if migrate_reads:
            for event in self.data["events"]:
                if event.get("status") not in WAITING | ERRORS:
                    self.data["readVersions"].setdefault(event["id"], version(event))
        for track in self.data["tracks"]:
            track.setdefault("revision", uuid.uuid4().hex)
            if track.get("status") == "checking":
                track.update(status="pending", nextCheckAt="", error="上次检查被中断，等待重新检查")
        self._save()

    def _save(self):
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        while len(payload.encode("utf-8")) > MAX_STATE_BYTES:
            # Only generated history is evicted; user-authored tracks and original
            # files are never deleted. Evidence previews keep active state bounded.
            if self.data["reports"]:
                self.data["reports"] = self.data["reports"][:len(self.data["reports"]) // 2]
            elif self.data["history"]:
                self.data["history"] = self.data["history"][len(self.data["history"]) // 2 + 1:]
            else:
                raise ValueError("总览数据超过安全容量，请缩小事项范围")
            self.data["retentionNote"] = "缓存达到容量上限，较早的生成记录已自动移除；事项和原文件未改变。"
            payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        atomic_write_private(self.path, payload)
        self._last_save = time.monotonic()

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.data)
            result["events"] = decorate(result["events"], result["readVersions"])
            return result

    def dismiss_event(self, identifier, fingerprint=None):
        """忽略一条动态（B-11）：立即从列表移除并持久化；同指纹不再出现，
        内容真变（新指纹）允许重现——「关掉这条提醒」而不是「永久屏蔽这件事」。"""
        with self.lock:
            if not isinstance(identifier, str) or not identifier or len(identifier) > 300:
                raise ValueError("这条动态已不可用，请刷新后查看")
            dismissed = self.data.setdefault("dismissed", {})
            if len(dismissed) >= 200:  # 有界：最老的忽略记录先出列
                dismissed.pop(next(iter(dismissed)), None)
            dismissed[identifier] = fingerprint if isinstance(fingerprint, str) else None
            self.data["events"] = [e for e in self.data["events"] if e.get("id") != identifier]
            self._save()

    def _is_dismissed(self, event) -> bool:
        """事件是否已被忽略（指纹不变仍忽略；指纹变了视为新内容，清记录并放行）。"""
        dismissed = self.data.get("dismissed") or {}
        event_id = event.get("id")
        if event_id not in dismissed:
            return False
        saved = dismissed[event_id]
        if saved is None or saved == event.get("fingerprint"):
            return True
        del dismissed[event_id]
        return False

    def mark_read(self, identifier, expected_version):
        with self.lock:
            event = next((item for item in self.data["events"] if item["id"] == identifier), None)
            if event is None or not isinstance(expected_version, str):
                raise ValueError("这条动态已不可用，请刷新后查看")
            current = version(event)
            if current != expected_version:
                return {"acknowledged": False, "reason": "动态已更新，未将新内容误标已读"}
            if self.data["readVersions"].get(identifier) != current:
                previous = self.data["readVersions"].get(identifier)
                self.data["readVersions"][identifier] = current
                try:
                    self._save()
                except Exception:
                    if previous is None:
                        self.data["readVersions"].pop(identifier, None)
                    else:
                        self.data["readVersions"][identifier] = previous
                    raise
            return {"acknowledged": True}

    def settings_update(self, payload):
        with self.lock:
            settings = self.data["settings"]
            for field, allowed in (("palette", PALETTES), ("motion", {"system", "reduced"}),
                                   ("dock", {"side", "notch", "pet"})):
                if field in payload:
                    if payload[field] not in allowed:
                        raise ValueError("不支持的显示设置")
                    settings[field] = payload[field]
            if "aiDaily" in payload:
                settings["aiDaily"] = payload["aiDaily"] is True
            self._save()

    def enable(self, connector, enabled):
        if connector not in ("otty", "browser", "calendar", "wechat", "hi"):
            raise ValueError("此来源尚无可靠连接器")
        with self.lock:
            self._source_versions[connector] = self._source_versions.get(connector, 0) + 1
            self._observation_baselines.pop(connector, None)
            self.data["settings"]["connectors"][connector] = bool(enabled)
            if not enabled:
                self._forget_source(connector)
            self._save()

    def source_revision(self, connector):
        with self.lock:
            return self._source_versions.get(connector, 0)

    def _forget_source(self, connector):
        self._observation_baselines.pop(connector, None)
        identifiers = {value for e in self.data["events"] if e.get("source") == connector
                       for value in (e["id"], e.get("sourceId")) if value}
        self.data["events"] = [e for e in self.data["events"] if e.get("source") != connector]
        for identifier in identifiers:
            self.data["readVersions"].pop(identifier, None)
        self.data["history"] = [e for e in self.data["history"] if e.get("source") != connector]
        # Reports are derived snapshots, not user-authored documents. Regenerate
        # only from still-authorized sources after disconnect/revocation.
        self.data["reports"] = []
        for track in self.data["tracks"]:
            if any((s["type"] == "connector" and (s["locator"] in identifiers
                                                  or s["locator"].startswith(connector + ":")))
                   or (s["type"] == "url" and connector == "browser") for s in track["sources"]):
                track.update(evidence=[], conclusion="来源连接已关闭，请重新连接后检查", digest="",
                             status="unavailable", revision=uuid.uuid4().hex, aiSummary=False)

    def _forget_event(self, event):
        # A changed site-authorization scope needs a fresh complete baseline.
        # In particular, a later disappearance must not restore revoked content.
        self._observation_baselines.pop(event.get("source"), None)
        identifiers = {event["id"], event.get("sourceId")}
        url = event.get("target", {}).get("url")
        self.data["history"] = [e for e in self.data["history"] if e.get("id") not in identifiers]
        self.data["reports"] = []
        for track in self.data["tracks"]:
            if any(s["locator"] in identifiers or (url and s["locator"] == url) for s in track["sources"]):
                track.update(evidence=[], conclusion="此页面授权已撤回，请重新授权后检查", digest="",
                             status="unavailable", revision=uuid.uuid4().hex, aiSummary=False)

    def calendars_update(self, ids):
        if (not isinstance(ids, list) or len(ids) > 100
                or any(not isinstance(i, str) or not i or len(i) > 1024 for i in ids)):
            raise ValueError("请选择有效日历")
        with self.lock:
            selected = list(dict.fromkeys(ids))
            if self.data["settings"].get("calendarIds") == selected:
                return False
            self._source_versions["calendar"] = self._source_versions.get("calendar", 0) + 1
            self.data["settings"]["calendarIds"] = selected
            # Selection is an authorization scope, not just a display filter.
            # Drop generated calendar snapshots while preserving user-authored
            # tracks/goals/sources. A fresh generation will repopulate only the
            # selected calendars; no older worker may restore revoked evidence.
            self._forget_source("calendar")
            self._save()
            return True

    def observe(self, source, result, *, revision=None):
        with self.lock:
            if not self.data["settings"]["connectors"].get(source):
                return
            if revision is not None and revision != self._source_versions.get(source, 0):
                return
            if not isinstance(result, dict):
                return
            status = result.get("status")
            if status == "permission_required":
                if source not in self._revoked_sources:
                    self._forget_source(source)
                    self._revoked_sources.add(source)
                    self._save()
                return
            self._revoked_sources.discard(source)
            disconnected = status in {"disconnected", "not_running", "not_connected", "disabled"}
            if disconnected:
                self._observation_baselines.pop(source, None)
            baseline = self._observation_baselines.get(source)
            old = {e["id"]: e for e in self.data["events"] if e.get("source") == source}
            incoming_events = result.get("events")
            authoritative = (status in {"ready", "connected"}
                             or source == "otty" and status == "partial" and result.get("presenceComplete") is True)
            complete = (authoritative and isinstance(incoming_events, list)
                        and len(incoming_events) <= 200 and not result.get("incomplete")
                        and not result.get("truncated") and not result.get("error"))
            valid_events = []
            seen = set()
            for incoming in incoming_events[:200] if isinstance(incoming_events, list) else []:
                if (not isinstance(incoming, dict) or not isinstance(incoming.get("id"), str)
                        or not incoming["id"] or incoming["id"] in seen):
                    complete = False
                    continue
                seen.add(incoming["id"])
                if (incoming.get("stale") or incoming.get("incomplete")
                        or incoming.get("status", incoming.get("state")) == "permission_required"):
                    complete = False
                valid_events.append(incoming)
            events = []
            substantive_change = False
            for incoming in valid_events:
                event = copy.deepcopy(incoming)
                event.update(source=source, status=event.get("status", event.get("state", "unknown")),
                             occurredAt=event.get("updatedAt", result.get("checkedAt", now())))
                previous_state = old.get(event["id"], {}).get("status")
                if event["status"] == "permission_required" and previous_state != "permission_required":
                    self._forget_event(event)
                    baseline = None
                    substantive_change = True
                if isinstance(event.get("evidence"), dict):
                    evidence = event["evidence"]
                    event["evidence"] = [{**evidence, "label": "网页读取范围",
                                          "text": evidence.get("coverage", "尚未读取") }]
                event["evidence"] = evidence_preview(event.get("evidence", []))
                reliable = (status in {"ready", "connected", "partial"} and not incoming.get("stale")
                            and not incoming.get("incomplete") and event["status"] != "permission_required")
                event["incomplete"] = not complete
                if not reliable:
                    event["stale"] = True
                previous = baseline.get(event["id"]) if baseline is not None else None
                # A partial snapshot can update an already-known item's actual
                # content, but cannot replace the complete presence ID set.
                changed = reliable and previous and any(previous.get(k) != event.get(k)
                                           for k in ("title", "summary", "status", "fingerprint",
                                                     "startAt", "endAt", "allDay", "calendarId", "location"))
                if changed:
                    self._record_observation(event, "changed")
                    substantive_change = True
                if previous is not None and reliable:
                    baseline[event["id"]] = copy.deepcopy(event)
                events.append(event)
            if complete:
                current = {e["id"]: e for e in events}
                if baseline is not None:
                    for identifier in (key for key in current if key not in baseline):
                        self._record_observation(current[identifier], "appeared",
                                                 "新出现于此来源的当前列表；不代表刚创建或任务完成。")
                        substantive_change = True
                    for identifier in (key for key in baseline if key not in current):
                        self._record_observation({**baseline[identifier], "state": "unknown", "status": "unknown"},
                                                 "removed", "已移出当前列表；仅表示本次完整检查未再看到，"
                                                 "不代表任务已完成。")
                        substantive_change = True
                self._observation_baselines[source] = copy.deepcopy(current)
            elif not disconnected:
                # Missing rows in a partial/error response are still last-known
                # rows, not proof of disappearance. Preserve them as stale.
                events.extend({**event, "incomplete": True, "stale": True}
                              for identifier, event in old.items() if identifier not in seen)
            # B-11：被忽略的动态不再入库（内容真变者除外，见 _is_dismissed）
            events = [e for e in events if not self._is_dismissed(e)]
            others = [e for e in self.data["events"] if e.get("source") != source]
            self.data["events"] = others + events
            reads = self.data["readVersions"]
            if baseline is None:
                for event in events:
                    if event.get("status") not in WAITING | ERRORS and not event.get("unreadCount"):
                        reads.setdefault(event["id"], version(event))
            # Bound bookkeeping to current events; no growing tombstone map.
            current_ids = {event["id"] for event in self.data["events"]}
            self.data["readVersions"] = {key: value for key, value in reads.items() if key in current_ids}
            cutoff = (datetime.now().astimezone() - timedelta(days=31)).isoformat()
            self.data["history"] = [e for e in self.data["history"]
                                    if e.get("observedAt", "") > cutoff][-1500:]
            if substantive_change or set(old) != {e["id"] for e in events} or time.monotonic() - self._last_save > 30:
                self._save()

    def _record_observation(self, event, change_type, summary=None):
        observation = {**copy.deepcopy(event), "changeType": change_type, "observedAt": now(),
                       "evidence": evidence_preview(event.get("evidence", []), 800)}
        if summary is not None:
            previous_summary = short_text(event.get("summary"), 2000)
            qualifier = " 最近一次可见信息：" if change_type == "removed" else " 来源摘要："
            observation["summary"] = summary + (qualifier + previous_summary if previous_summary else "")
        self.data["history"].append(observation)

    def track_save(self, payload, *, create=False):
        title = short_text(payload.get("title"), 120)
        if not title:
            raise ValueError("先给要推进的事项起个名字")
        frequency = payload.get("frequency", "hourly")
        if frequency not in FREQUENCIES:
            raise ValueError("请选择刷新频率")
        sources = payload.get("sources", [])
        if not isinstance(sources, list) or not 1 <= len(sources) <= 12:
            raise ValueError("请选择 1–12 个真实追踪渠道")
        cleaned = []
        with self.lock:
            for source in sources:
                if not isinstance(source, dict) or source.get("type") not in ("url", "file", "connector"):
                    raise ValueError("不支持的追踪渠道")
                locator = short_text(source.get("locator"), 2048)
                if not locator:
                    raise ValueError("追踪渠道不能为空")
                if source["type"] == "file" and not any(
                    locator in (f["id"], f["path"]) for f in self.data["files"]
                ):
                    raise ValueError("请先通过文件条选择文件或文件夹，再添加追踪")
                if source["type"] == "url":
                    from urllib.parse import urlsplit
                    url = urlsplit(locator)
                    if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
                        raise ValueError("网页渠道需要不含账号密码的 http/https 地址")
                cleaned.append({"id": short_text(source.get("id") or uuid.uuid4().hex, 80),
                                "type": source["type"], "label": short_text(source.get("label") or locator, 120),
                                "locator": locator})
            if create:
                if len(self.data["tracks"]) >= 60:
                    raise ValueError("最多同时保留 60 个事项，请归档或删除不再需要的事项")
                track = {"id": uuid.uuid4().hex, "createdAt": now(), "paused": False, "completed": False,
                         "status": "pending", "conclusion": "尚未检查", "evidence": []}
                self.data["tracks"].append(track)
            else:
                track = self._track(payload.get("id"))
            track.update(title=title, goal=short_text(payload.get("goal"), 3000), frequency=frequency,
                         aiEnabled=payload.get("aiEnabled") is True, sources=cleaned, updatedAt=now(),
                         nextCheckAt="", revision=uuid.uuid4().hex)
            if "completed" in payload:
                track["completed"] = payload["completed"] is True
            self._save()
            return copy.deepcopy(track)

    def _track(self, identifier):
        for track in self.data["tracks"]:
            if track["id"] == identifier:
                return track
        raise ValueError("此事项已不存在")

    def track_pause(self, identifier, paused):
        with self.lock:
            track = self._track(identifier)
            track.update(paused=bool(paused), revision=uuid.uuid4().hex)
            if track.get("status") == "checking":
                track["status"] = "paused" if paused else "pending"
            self._save()

    def track_delete(self, identifier):
        with self.lock:
            self._track(identifier)
            self.data["tracks"] = [t for t in self.data["tracks"] if t["id"] != identifier]
            self._save()

    def track_result(self, identifier, result, *, revision=None):
        with self.lock:
            try:
                track = self._track(identifier)
            except ValueError:
                return
            if revision is not None and track["revision"] != revision:
                return  # An older in-flight refresh must not overwrite edited sources/goal.
            track.update(result)
            if "evidence" in result:
                track["evidence"] = evidence_preview(result["evidence"])
            seconds = FREQUENCIES[track["frequency"]]
            track["nextCheckAt"] = (datetime.now().astimezone() + timedelta(seconds=seconds)).isoformat()
            self._save()

    def add_files(self, paths):
        added = []
        with self.lock:
            for raw in paths[:30]:
                path = Path(raw).expanduser().resolve(strict=True)
                if not (path.is_file() or path.is_dir()):
                    raise ValueError("请选择普通文件或文件夹")
                if path == Path(path.anchor) or path == Path.home():
                    raise ValueError("请拖入具体工作目录，不要选择整个磁盘或用户主目录")
                existing = next((f for f in self.data["files"] if f["path"] == str(path)), None)
                if existing:
                    added.append(existing)
                    continue
                if len(self.data["files"]) >= 30:
                    raise ValueError("文件条最多保留 30 个入口")
                item = {"id": uuid.uuid4().hex, "name": path.name, "path": str(path),
                        "directory": path.is_dir(), "addedAt": now()}
                self.data["files"].append(item)
                added.append(item)
            self._save()
        return copy.deepcopy(added)

    def remove_file(self, identifier):
        with self.lock:
            self.data["files"] = [f for f in self.data["files"] if f["id"] != identifier]
            self._save()

    def put_report(self, report):
        with self.lock:
            report = copy.deepcopy(report)
            for section in report.get("sections", []):
                for item in section.get("items", []):
                    item["evidence"] = evidence_preview(item.get("evidence", []), 400)
            self.data["reports"] = sorted(
                [r for r in self.data["reports"] if r["date"] != report["date"]] + [report],
                key=lambda r: r["date"], reverse=True)[:31]
            self._save()
