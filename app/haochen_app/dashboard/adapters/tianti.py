"""天梯日报（创能天梯 cowork）只读连接：拉取核心事实卡与每日进展。

鉴权 = 内置只读 API Key（官方 API 文档公开）+ 用户本人的 web_session SSO cookie。
cookie 只从 Keychain 读，由内嵌登录窗写入（tianti_auth.py），
本适配器绝不引导用户去 DevTools。

身份邮箱走 hi 的既有流程（hi search:me → xhsContactId），不另建账号体系。
读取结果同时落一份缓存（tianti_cache.json），供对话侧「深度阅读日报」工具使用。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ...keychain import KeychainStore, read_credential_without_ui
from ..store import now
from .hi import HiError, _find_cli
from .hi import _run as _hi_run

log = logging.getLogger("haochen.tianti")

BASE = "https://cowork.xiaohongshu.com/s/teach-2-v3"
REPORT_URL = f"{BASE}/api/daily/external/report"
DAILY_PAGE = f"{BASE}/#daily"
# 内部系统的只读 API key（天梯日报官方 API 文档公开），不是用户级秘密
_API_KEY = "6KKao3wANfvTsLGCJjX_TUvs_Hs_QYvmMiwtyPFJ61U"
COOKIE_PROVIDER = "tianti"          # Keychain 里的条目名
CACHE_NAME = "tianti_cache.json"    # 落在 home/dashboard/ 下，供 read_daily 工具读
_TIMEOUT = 15.0


class CookieExpired(Exception):
    """SSO cookie 失效（401/403）——引导用户一键重连。"""


def fact_card_url(project_id: str) -> str:
    """核心事实卡的独立链接（用户确认存在；锚点格式以日报页实际为准）。"""
    return f"{BASE}/#daily?project={project_id}"


def _http_get(url: str, *, cookie: str, timeout: float = _TIMEOUT) -> dict:
    request = urllib.request.Request(url, headers={
        "X-API-Key": _API_KEY,
        "Cookie": f"web_session={cookie}",
        "User-Agent": "haochen/0.6",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise CookieExpired("cookie 已失效") from exc
        raise ConnectionError(f"天梯日报服务返回 HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ConnectionError("网络连接失败，请检查内网连接") from exc
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConnectionError("天梯日报返回了无法识别的数据") from exc
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise ConnectionError("天梯日报返回结构不符合契约")
    return data


class TiantiAdapter:
    """天梯日报连接器。runner/fetcher 可注入（测试隔离真实网络与 CLI）。"""

    def __init__(self, home: Path, keychain=None, runner=None, fetcher=None):
        self._home = Path(home)
        self._keychain = keychain or KeychainStore()
        self._runner = runner or _hi_run
        self._fetch = fetcher or (lambda email, cookie: _http_get(
            f"{REPORT_URL}?user_email={urllib.parse.quote(email)}", cookie=cookie))

    # ── 凭证与身份 ──────────────────────────────────────────────

    def cookie_present(self) -> bool:
        try:
            return bool(read_credential_without_ui(self._keychain, COOKIE_PROVIDER))
        except Exception:  # noqa: BLE001 - Keychain 不可用按无 cookie 处理
            return False

    def save_cookie(self, value: str) -> None:
        """内嵌登录窗的唯一落点（零插件：日报不再需要浏览器扩展）。"""
        value = (value or "").strip()
        if not value:
            raise ValueError("cookie 为空")
        self._keychain.set(COOKIE_PROVIDER, value)

    def _cookie(self) -> str:
        try:
            return read_credential_without_ui(self._keychain, COOKIE_PROVIDER) or ""
        except Exception:  # noqa: BLE001
            return ""

    def _email(self) -> str:
        """走 hi 的获取流程拿当前用户邮箱（hi search:me → xhsContactId）。"""
        cli = _find_cli()
        if not cli:
            raise HiError("cli_missing", "未找到内部 hi CLI；天梯日报需要它来确认你的身份邮箱。")
        identity = self._runner(cli, "search:me")
        if not isinstance(identity, dict) or not identity.get("xhsContactId"):
            raise HiError("not_authenticated", "Hi 登录态失效；请在终端重新登录 hi 后重试。")
        return str(identity["xhsContactId"]).strip()

    # ── 主快照 ──────────────────────────────────────────────────

    def snapshot(self) -> dict:
        checked = now()
        if not self.cookie_present():
            log.info("tianti: no cookie in keychain")
            return {
                "status": "permission_required", "checkedAt": checked, "events": [],
                "reasonCode": "cookie_missing",
                "message": "一键连接天梯日报：点「连接」后会自动完成授权，无需手动找 cookie。",
            }
        try:
            email = self._email()
        except HiError as exc:
            log.warning("tianti: email via hi failed: %s", exc.code)
            return {
                "status": "partial", "checkedAt": checked, "events": [],
                "reasonCode": exc.code, "message": f"天梯日报连接已建立，但身份邮箱获取失败：{exc}",
            }
        try:
            data = self._fetch(email, self._cookie())
        except CookieExpired:
            log.info("tianti: cookie rejected by server (401/403)")
            return {
                "status": "permission_required", "checkedAt": checked, "events": [],
                "reasonCode": "cookie_expired",
                "message": "天梯日报登录态已过期，点「连接」一键重新授权即可。",
            }
        except ConnectionError as exc:
            log.warning("tianti: fetch failed: %s", exc)
            return {"status": "error", "checkedAt": checked, "events": [],
                    "reasonCode": "fetch_failed", "message": str(exc)}

        self._write_cache(data)
        events = self._normalize_events(data)
        projects = self._normalize_projects(data)
        return {
            "status": "ready", "checkedAt": checked, "events": events,
            "projects": projects, "email": email,
            "message": f"已连接天梯日报，{len(projects)} 个项目在册。",
        }

    # ── 归一化 ──────────────────────────────────────────────────

    def _normalize_projects(self, data: dict) -> list[dict]:
        """核心事实卡（正式项目；misc- 前缀的「其他」事项不算档案，不同步）。结果按项目名排序。"""
        projects = []
        for p in data.get("projects", []):
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or "").strip()
            name = str(p.get("name") or "").strip()
            if not pid or not name or pid.startswith("misc-"):
                continue
            projects.append({
                "id": pid, "name": name,
                "goal": str(p.get("goal") or "").strip(),
                "desc": str(p.get("desc") or "").strip(),
                "stage": str(p.get("stage") or "").strip(),
                "status": str(p.get("status") or "").strip(),
                "doc": str(p.get("doc") or "").strip(),
                "link": fact_card_url(pid),
            })
        projects.sort(key=lambda p: p["name"])
        return projects

    def _normalize_events(self, data: dict) -> list[dict]:
        """每个正式项目 → 一张动态卡（最近一个有内容的日子）。"""
        events = []
        for p in data.get("projects", []):
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or "").strip()
            name = str(p.get("name") or "").strip()
            if not pid or not name or pid.startswith("misc-"):
                continue
            days = p.get("days") if isinstance(p.get("days"), dict) else {}
            latest_date = max(days.keys()) if days else ""
            day = days.get(latest_date) if latest_date else None
            if not isinstance(day, dict):
                continue
            points = [str(pt.get("text")).strip()
                      for pt in (day.get("prog") or {}).get("points", [])
                      if isinstance(pt, dict) and pt.get("text")]
            todos = [str(t.get("tx")).strip()
                     for t in day.get("todos", []) if isinstance(t, dict) and t.get("tx")]
            risk = day.get("risk") if isinstance(day.get("risk"), dict) else {}
            risk_level = str(risk.get("level") or "good")
            risk_read = str(risk.get("read") or "").strip()
            progress = day.get("progress")
            head = points[0] if points else str(day.get("progressReason") or "").strip()
            summary_parts = [part for part in (
                head,
                f"进度 {progress}%" if isinstance(progress, (int, float)) else "",
                f"{len(todos)} 项待办" if todos else "",
                f"风险：{risk_read}" if risk_level != "good" and risk_read else "",
            ) if part]
            if not summary_parts:
                continue
            state = "idle" if risk_level == "good" else "needs_attention"
            events.append({
                "id": f"tianti:{pid}", "source": "tianti", "title": name,
                "summary": "；".join(summary_parts)[:500],
                "state": state, "status": state,
                "updatedAt": f"{latest_date}T09:00:00+00:00" if latest_date else checked_now(),
                "occurredAt": f"{latest_date}T09:00:00+00:00" if latest_date else checked_now(),
                "target": {"kind": "url", "url": fact_card_url(pid)},
                "evidence": [
                    {"label": "来源", "text": "天梯日报（创能天梯）"},
                    {"label": "核心事实卡", "text": name, "url": fact_card_url(pid)},
                ],
            })
        return events

    def _write_cache(self, data: dict) -> None:
        """供对话侧 read_daily 工具深度阅读；0600 权限，失败不阻断主流程。"""
        try:
            from ...secure_storage import atomic_write_private
            cache_dir = self._home / "dashboard"
            cache_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_private(cache_dir / CACHE_NAME,
                                 json.dumps(data, ensure_ascii=False))
        except OSError as exc:
            log.warning("tianti cache write failed: %s", exc)


def checked_now() -> str:
    return now()
