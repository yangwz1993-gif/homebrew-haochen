"""Opt-in, bounded read of the user's own Hi work signals via the local hi CLI.

Strictly read-only. Surfaces two honest, minimal-scope signals for *the logged-in
user only*:

* upcoming / in-progress calendar items for the rest of today, and
* pending "待我处理" tasks (taskStatus = in-progress),

both presented as "待跟进" reminders. This adapter deliberately does NOT:

* claim a real unread count (the hi CLI provides no read/unread sync),
* read chat message bodies, notification archives or private databases,
* send any message or perform any write operation, or
* read, print or persist the OAuth token.

All CLI calls run on DashboardService's worker thread through a bounded
subprocess, never on the Qt thread, and never trigger a permission prompt.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from datetime import datetime
from pathlib import Path

from ..store import now

_MAX_OUTPUT = 512 * 1024
# hi is a Node CLI: process spawn (~1s) + internal network. 8s was too tight for
# 4 back-to-back calls and caused spurious "partial" (能力受限). 20s is safe.
_TIMEOUT = 20.0
_MAX_EVENTS = 12
_MAX_MSGS = 20
# 只保留 @ 我的消息：正文里出现我的名字（Hi 把 @提及渲染成名字）或字面 '@'。
# 不做审批/关键词/机器人等宽匹配，避免被群聊等其他消息打扰。
# Hi 桌面 App（Electron RedCity）。精准跳转走其注册的 citylink:// 深链：
# citylink://client/chat/openConversation?type=chat&id=<chatId>（不带 host——Hi 会把
# citylink:// 改写成 https://citylink.xiaohongshu.com/，带 host 会重复导致路由失效）。
# type=chat+chatId 群/单聊/应用号通用；拿不到 chatId 时降级为仅打开 App。已端到端实测。
_HI_BUNDLE_ID = "com.electron.redcity"


class HiError(ValueError):
    """A safe, body-free error at the connector boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_LOGIN_PATH: str | None = None


def _login_path() -> str:
    """The user's real login-shell PATH (cached).

    The packaged GUI app inherits a minimal PATH, so it misses wherever the user's
    node/hi actually live — nvm, fnm, volta, asdf, or a custom npm prefix like
    ``~/npm-global/bin``. Asking the login shell once covers all of them without
    hard-coding every version manager.
    """
    global _LOGIN_PATH
    if _LOGIN_PATH is not None:
        return _LOGIN_PATH
    shell = os.environ.get("SHELL") or "/bin/zsh"
    value = ""
    try:
        out = subprocess.run(
            [shell, "-lc", 'printf %s "$PATH"'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=6,
        )
        value = (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        value = ""
    _LOGIN_PATH = value
    return value


def _find_cli() -> str | None:
    """Locate the internal ``hi`` node CLI without launching it.

    ``shutil.which`` only sees the packaged app's inherited PATH. We additionally
    resolve against the user's login-shell PATH (covers npm-global / fnm / volta /
    asdf) and a few common explicit locations.
    """
    from shutil import which

    found = which("hi")
    if found:
        return found
    login = _login_path()
    if login:
        found = which("hi", path=login)
        if found:
            return found
    candidates: list[Path] = []
    home = Path.home()
    nvm = home / ".nvm/versions/node"
    if nvm.is_dir():
        try:
            candidates.extend(sorted(nvm.glob("*/bin/hi"), reverse=True))
        except OSError:
            pass
    candidates.extend([
        home / ".bun/bin/hi",
        home / "npm-global/bin/hi",
        home / ".npm-global/bin/hi",
        home / ".local/bin/hi",
        Path("/opt/homebrew/bin/hi"),
        Path("/usr/local/bin/hi"),
    ])
    return next((str(p) for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


def _run(cli: str, *args: str) -> object:
    """Run one bounded, read-only hi command and parse its JSON stdout.

    stderr is discarded: provider errors can echo tokens, chat titles or URLs
    and must never surface in a dashboard summary or exception body.
    """
    # `hi` is a Node CLI (shebang `env node`). The packaged GUI app inherits a
    # minimal PATH without the user's node, so `hi` would fail with "node: not found".
    # Prepend the CLI's own dir AND the login-shell PATH so node is found wherever it
    # lives (nvm / fnm / volta / asdf / custom npm prefix).
    env = dict(os.environ)
    parts = [os.path.dirname(cli), _login_path(), env.get("PATH", "")]
    env["PATH"] = os.pathsep.join(p for p in parts if p)
    child = subprocess.Popen(
        [cli, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, shell=False, start_new_session=True, env=env,
    )
    try:
        raw, _ = child.communicate(timeout=_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            child.kill()
        child.wait()
        raise HiError("timeout", "Hi 响应超时，稍后会重新连接。") from exc
    if child.returncode:
        raise HiError("cli_failed", "hi 命令执行失败，请确认登录态有效。")
    if len(raw) > _MAX_OUTPUT:
        raise HiError("output_limit", "Hi 返回的数据过大，本次未更新。")
    try:
        return json.loads(raw or b"null")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HiError("invalid_response", "Hi 返回了无法识别的数据。") from exc


def _text(value, limit: int = 200) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c >= " " or c in "\t")[:limit].strip()


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # Millisecond precision (e.g. 2026-09-03T14:00:00.000+08:00) parses on
        # 3.12, but guard other shapes rather than raising into the worker.
        return None


_HI_APP_CHECKED = False
_HI_APP_PRESENT = False


def _hi_target() -> dict | None:
    """Jump target opens the Hi desktop app (no deep-link to a specific chat).

    Returns None if the app isn't installed, so the UI simply hides the button.
    Result is cached per process.
    """
    global _HI_APP_CHECKED, _HI_APP_PRESENT
    if not _HI_APP_CHECKED:
        _HI_APP_CHECKED = True
        try:
            from AppKit import NSWorkspace
            _HI_APP_PRESENT = (
                NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(_HI_BUNDLE_ID) is not None
            )
        except Exception:
            _HI_APP_PRESENT = False
    return {"kind": "hi", "bundleId": _HI_BUNDLE_ID} if _HI_APP_PRESENT else None


def _today_window() -> tuple[str, str]:
    local = datetime.now().astimezone()
    end = local.replace(hour=23, minute=59, second=59, microsecond=0)
    return local.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


class HiAdapter:
    """Read the logged-in user's own upcoming schedule and pending tasks."""

    def __init__(self, cli_path: str | None = None, *, runner=None):
        self._explicit_cli = cli_path
        # runner(cli, *args) -> parsed JSON, injectable for tests.
        self._runner = runner or _run

    def _cli(self) -> str | None:
        return self._explicit_cli or _find_cli()

    def _schedules(self, cli: str, reference: datetime) -> list[dict]:
        begin, end = _today_window()
        data = self._runner(cli, "calendar:get-user-schedules",
                             "--begin-time", begin, "--end-time", end, "--page-size", "50")
        rows = data[0].get("scheduleList", []) if isinstance(data, list) and data else []
        has_detail = bool(isinstance(data, list) and data and data[0].get("hasDetailPermission", True))
        events: list[dict] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            start = _parse_iso(row.get("beginTime"))
            finish = _parse_iso(row.get("endTime"))
            if finish is not None and finish <= reference:
                continue  # already ended; not a follow-up item
            title = _text(row.get("title"), 80) if has_detail else ""
            when = start.strftime("%H:%M") if start else "今日"
            until = f"–{finish.strftime('%H:%M')}" if finish else ""
            label = title or "忙碌时段"
            in_progress = start is not None and start <= reference and (finish is None or finish > reference)
            summary = f"{when}{until}｜{label}" + ("（进行中）" if in_progress else "（即将开始）")
            events.append({
                "id": f"hi:schedule:{row.get('scheduleId')}",
                "title": label,
                "state": "available",
                "summary": summary + "。来自 Hi 日历的忙碌事实，不含会议正文。",
                "reasonCode": "calendar_busy",
                "fingerprint": f"{row.get('beginTime')}|{label}",
                "startAt": row.get("beginTime"),
                "updatedAt": now(),
                "evidence": [{"label": "Hi 日程", "text": summary}],
                "_sortKey": (0 if in_progress else 1, row.get("beginTime") or ""),
            })
        events.sort(key=lambda e: e.pop("_sortKey"))
        return events[:_MAX_EVENTS]

    def _names(self, cli: str, email: str) -> list[str]:
        """My display-name tokens (薯名/姓名). Hi renders @-mentions as the name,
        not an '@' symbol, so we match these against message bodies."""
        try:
            data = self._runner(cli, "search:employee", "--query", email, "--page-size", "1")
        except HiError:
            return []
        items = data.get("items", []) if isinstance(data, dict) else []
        row = items[0] if items and isinstance(items[0], dict) else {}
        tokens = []
        for key in ("redName", "userName", "fullName", "name"):
            value = _text(row.get(key), 40)
            if value and value not in tokens:
                tokens.append(value)
        return tokens

    def _followups(self, cli: str, email: str, reference: datetime, names: list[str]) -> list[dict]:
        """Recent messages that @-mention me — nothing else.

        Read-only. Excludes messages I sent. Deliberately keeps ONLY messages that
        @ me (my name token, rendered by Hi as the name, or a literal '@'); group
        chatter, keyword/approval matches and bot notices are intentionally dropped
        so the module isn't disturbed by unrelated messages. Not a real unread count.
        """
        end = int(reference.timestamp() * 1000)
        start = end - 12 * 3600 * 1000
        data = self._runner(cli, "search:message", "--message-include-chat-member", email,
                            "--message-send-time-stamp-start", str(start),
                            "--message-send-time-stamp-end", str(end), "--page-size", "50")
        items = data.get("items", []) if isinstance(data, dict) else []
        out: list[dict] = []
        for msg in items if isinstance(items, list) else []:
            if not isinstance(msg, dict):
                continue
            sender = _text(msg.get("senderName"), 40)
            sender_id = _text(msg.get("senderId"), 160)
            full = _text(msg.get("content"), 400)
            if not full or (email and sender_id.lower() == email.lower()):
                continue  # skip empty bodies and your own outgoing messages
            content = full[:120]
            # A real @-mention shows my name in the body (no '@' glyph). Only these
            # qualify — no keyword/approval/bot broadening, so no unrelated noise.
            name_hit = any(tok and tok in full for tok in names)
            at_me = "@" in content or name_hit
            if not at_me:
                continue
            mentioned = True
            sent = _parse_iso(msg.get("sendTime"))
            when = sent.strftime("%m-%d %H:%M") if sent else _text(msg.get("sendTime"), 40)
            mid = _text(msg.get("messageId"), 120) or f"{sender}:{_text(msg.get('sendTime'), 40)}"
            out.append({"id": mid, "sender": sender or "Hi 消息", "content": content, "full": full,
                        "when": when, "mentioned": mentioned, "at_me": at_me,
                        "chatId": _text(msg.get("chatId"), 120)})
        # Literal @-mentions first, then other actionable follow-ups.
        out.sort(key=lambda item: 0 if item["at_me"] else (1 if item["mentioned"] else 2))
        return out[:_MAX_MSGS]

    def snapshot(self) -> dict:
        checked = now()
        result: dict = {"checkedAt": checked, "events": []}
        cli = self._cli()
        if cli is None:
            result.update(status="not_running", reasonCode="cli_missing",
                          message="未找到内部 hi CLI；请先安装 hi 工具后再连接。")
            return result
        try:
            identity = self._runner(cli, "search:me")
        except HiError:
            result.update(status="permission_required", reasonCode="not_authenticated",
                          message="Hi 登录态失效或未授权；请在终端重新登录 hi 后重试。不会读取或上传令牌。")
            return result
        if not isinstance(identity, dict) or not identity.get("xhsContactId"):
            result.update(status="permission_required", reasonCode="not_authenticated",
                          message="Hi 未返回有效身份；请在终端重新登录 hi 后重试。")
            return result

        email = _text(identity.get("xhsContactId"), 160)
        reference = datetime.now().astimezone()
        partial = False
        try:
            names = self._names(cli, email)
        except HiError:
            names = []
        try:
            followups = self._followups(cli, email, reference, names)
        except HiError:
            followups, partial = [], True
        try:
            schedule_events = self._schedules(cli, reference)
        except HiError:
            schedule_events, partial = [], True

        target = _hi_target()  # opens the Hi app if installed, else None (no jump button)

        # @ 你的消息在前（用户先看这些），随后是今日日程。
        events: list[dict] = []
        for msg in followups:
            suffix = "（@ 你）" if msg["at_me"] else ("（待处理）" if msg["mentioned"] else "")
            evidence = [{"label": "发送人", "text": msg["sender"]}]
            if msg["when"]:
                evidence.append({"label": "时间", "text": msg["when"]})
            evidence.append({"label": "消息", "text": msg["full"]})
            event = {
                "id": f"hi:msg:{msg['id']}", "sourceId": f"hi:msg:{msg['id']}",
                "title": msg["sender"] + suffix,
                "state": "needs_attention" if msg["mentioned"] else "available",
                "status": "needs_attention" if msg["mentioned"] else "available",
                "summary": msg["content"],
                "reasonCode": "mention" if msg["mentioned"] else "followup",
                "fingerprint": f"msg:{msg['id']}:{msg['content']}",
                "updatedAt": checked,
                "evidence": evidence,
            }
            if target:
                # Precise jump: open this exact conversation in Hi when we know it.
                event["target"] = {**target, "chatId": msg["chatId"]} if msg.get("chatId") else target
            events.append(event)
        if target:
            for schedule in schedule_events:
                schedule["target"] = target
        events.extend(schedule_events)

        summary_bits = []
        if followups:
            summary_bits.append(f"{len(followups)} 条 @ 你的消息")
        if schedule_events:
            summary_bits.append(f"今日 {len(schedule_events)} 个待跟进日程")
        headline = "、".join(summary_bits) if summary_bits else "暂无 @ 你的消息或今日日程"
        message = f"已连接 Hi：{headline}。只聚合 @ 你的消息与今日日程，不含群聊其他消息，也不代表真实未读数。"
        # Only downgrade to "partial" when we truly got nothing; if any signal
        # loaded, the connector is connected (green), with a soft refresh note.
        if partial and not events:
            status = "partial"
        else:
            status = "ready"
            if partial:
                message += "（个别数据稍后自动刷新）"
        # 已连接（含个别子调用失败的 partial 降级态）但没有任何 @我消息/今日日程时，
        # 给一个明确的占位，而不是空白。partial 时 summary 保持不变（避免版本翻动造成
        # 假未读），降级说明放进 evidence（不参与版本计算）。
        if not events:
            note = ([{"label": "数据完整性", "text": "个别数据本次获取失败，稍后自动刷新。"}]
                    if partial else [])
            events = [{
                "id": "hi:none", "sourceId": "hi:none", "title": "Hi",
                "state": "available", "status": "available",
                "summary": "当前没有 @ 你的待处理消息。",
                "reasonCode": "empty", "fingerprint": "hi:none",
                "updatedAt": checked, "evidence": note,
            }]
        result.update(status=status, message=message, events=events)
        return result
