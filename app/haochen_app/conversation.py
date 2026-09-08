"""单回合结果协议（brief + detail）与兼容解析。

一个模型回合同时产出【brief】和【detail】：桌宠显示 brief，完整窗口显示
brief + detail。旧【answer】/【summary】历史仍可解析，但控制器不再发送第二次
summary 踢令。

用法：

    ctrl = ConversationController(client)     # client = EngineClient（已 start）
    ctrl.answer_delta.connect(...)            # str: 原始结果流式增量（打字机用）
    ctrl.answer_done.connect(...)             # str: detail（兼容信号）
    ctrl.summary_done.connect(...)            # str: brief（兼容信号）
    ctrl.turn_done.connect(...)               # TurnResult: 同一回合结构化结果
    ctrl.failed.connect(...)                  # str: 错误描述
    ctrl.send("帮我看看这个页面")              # 发起一轮

注意：`haochen-summary-phase` 仅为旧历史兼容标记，UI 永不渲染。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from .engine_client import EngineClient

SUMMARY_KICK_PREFIX = "haochen-summary-phase"
# 配对标记：兼容模型的错误闭合——==answer==/==/answer==、==summary==/==/summary== 等
# 变体视同【】系标记（v0.1.10：真机观测到模型【answer】开头却 ==/answer== 收尾）。
_PAIRS = {
    kind: re.compile(
        rf"(?:【{kind}】|=={kind}==)\s*(.*?)\s*(?:【/{kind}】|==/{kind}==)", re.S
    )
    for kind in ("brief", "detail", "answer", "summary")
}
_ANY_TAG = re.compile(r"【/?(?:brief|detail|answer|summary)】|==/?(?:brief|detail|answer|summary)==")
_ANY_CLOSE = r"(?:【/(?:brief|detail|answer|summary)】|==/(?:brief|detail|answer|summary)==)"


@dataclass(frozen=True, slots=True)
class TurnResult:
    """一次模型回合的两个展示层。"""

    brief: str
    detail: str
    raw: str = ""
    fallback_used: bool = False


def humanize_error(text: str) -> str:
    """把引擎错误（如 '402: {"message":"Insufficient Balance",...}'）转成用户能懂的文案。"""
    import json as _json

    raw = (text or "").strip()
    low = raw.lower()
    if any(token in low for token in (
        "no api key found", "api key not found", "missing api key", "use /login",
    )):
        return "当前模型还没有配置 API Key。请打开设置，保存并验证后再试。"
    if any(token in low for token in (
        "authorization", "invalid api key", "incorrect api key", "authentication",
        "unauthorized", "bearer sk-",
    )):
        return "API Key 无效或格式不正确。请打开设置重新配置后再试。"
    if any(token in low for token in ("timed out", "timeout", "connection refused", "network")):
        return "暂时连不上模型服务。请检查网络，稍后重试。"
    if "rate limit" in low or raw.startswith("429:"):
        return "请求太频繁，模型服务暂时限流。请稍等片刻再试。"
    # GUI 用户无法使用引擎 CLI 指令和包内文档路径。未知错误只要混入这些
    # 实现细节，就安全降级为可执行的用户提示，避免泄露本机绝对路径。
    if any(token in low for token in (
        "/private/", "/contents/resources/engine", "providers.md", "models.md",
        "\\contents\\resources\\engine", "\\private\\",
    )):
        return "模型连接失败。请打开设置检查 API Key 和模型，验证后再试。"
    m = re.match(r"^(\d{3}):\s*(\{.*\})\s*$", raw, re.S)
    if not m:
        return raw
    code, payload = m.group(1), m.group(2)
    try:
        message = str(_json.loads(payload).get("message") or payload)
    except ValueError:
        return raw
    if "insufficient balance" in message.lower():
        return f"API 账户余额不足（HTTP {code}）——请到服务商控制台充值，或在设置中更换 Key"
    if code in ("401", "403"):
        return "API Key 无效或没有访问权限。请打开设置检查配置。"
    return f"API 错误 {code}：{message}"


def strip_tags(text: str) -> str:
    return _ANY_TAG.sub("", text or "").strip()


def parse_paired(text: str, kind: str) -> str:
    """解析配对标记块，无配对或未知 kind 返回空字符串。"""
    pair = _PAIRS.get(kind)
    if pair is None:
        return ""
    parts = [p.strip() for p in pair.findall(text or "") if p.strip()]
    return "\n\n".join(parts).strip()


def parse_loose_block(text: str, kind: str) -> str:
    """Recover a block whose opening tag is correct but closing tag drifted."""
    if kind not in _PAIRS:
        return ""
    pattern = re.compile(rf"(?:【{kind}】|=={kind}==)\s*(.*?)\s*{_ANY_CLOSE}", re.S)
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def extractive_summary(text: str, limit: int = 160) -> str:
    """从详答抽高密度短结：优先首句结论 + 少量要点行（沿用上一版逻辑）。"""
    raw = strip_tags(text)
    if not raw:
        return ""
    lines = []
    for ln in raw.replace("**", "").replace("==", "").splitlines():
        s = ln.strip().lstrip("-•* ").strip()
        if s:
            lines.append(s)
    if not lines:
        t = " ".join(raw.split())
        return t[:limit] + ("…" if len(t) > limit else "")
    parts = [lines[0]]
    for ln in lines[1:]:
        if len(parts) >= 3:
            break
        if any(x in ln for x in ("我来", "好的", "首先", "接下来", "工具")):
            continue
        parts.append(ln)
    out = "；".join(parts) if len(parts) > 1 else parts[0]
    out = " ".join(out.split())
    if len(out) <= limit:
        return out
    cut = out[:limit]
    for sep in ("。", "；", "！", "？", ".", ";", "，"):
        i = cut.rfind(sep)
        if i >= limit // 2:
            return cut[: i + 1]
    return cut.rstrip("，,;；") + "…"


def normalize_brief(text: str) -> str:
    """结果卡已有“结论”标签，去掉模型重复生成的同名 Markdown 标题。"""
    return re.sub(
        r"^\s*(?:#{1,3}\s*)?(?:结论|简答|答案)(?:\s*[:：]\s*|\s*\n+)",
        "",
        text or "",
        count=1,
    ).strip()


def parse_turn_result(text: str) -> TurnResult:
    """优先解析新协议，并对旧协议或无标记回答做确定性降级。"""
    raw = text or ""
    current_brief = parse_paired(raw, "brief")
    current_detail = parse_paired(raw, "detail")
    detail = current_detail or parse_paired(raw, "answer") or parse_loose_block(raw, "detail")
    brief = current_brief or parse_paired(raw, "summary") or parse_loose_block(raw, "brief")
    fallback_used = not (current_brief and current_detail)
    if not detail:
        detail = strip_tags(raw)
    if not brief:
        brief = extractive_summary(detail)
    return TurnResult(
        brief=normalize_brief(brief),
        detail=detail,
        raw=raw,
        fallback_used=fallback_used,
    )


class ConversationController(QObject):
    """一轮对话的一次请求编排。UI 只订阅信号，不碰引擎事件细节。"""

    answer_delta = pyqtSignal(str)    # answer 阶段流式增量
    answer_done = pyqtSignal(str)     # answer 阶段完成（详答全文，已剥标记）
    summarizing = pyqtSignal()        # 兼容信号：结构化结果已开始收敛
    summary_done = pyqtSignal(str)    # brief 完成（解析值或兜底抽取）
    turn_done = pyqtSignal(object)    # TurnResult
    failed = pyqtSignal(str)          # 回合错误（stopReason=error 等）
    busy_changed = pyqtSignal(bool)   # 是否在一轮对话中
    request_accepted = pyqtSignal(str)
    request_committed = pyqtSignal(str)
    request_failed = pyqtSignal(str, str)

    def __init__(self, client: EngineClient, parent=None):
        super().__init__(parent)
        self.client = client
        self._phase = ""              # "" | "answer"
        self._answer_buf = ""
        self._answer_text = ""
        self._requests: dict[str, str] = {}
        self._answer_request_id: str | None = None
        self._answer_accepted = False
        self._user_message_seen = False
        client.event.connect(self._on_event)
        client.response.connect(self._on_response)
        client.crashed.connect(self._on_crashed)

    @property
    def busy(self) -> bool:
        return bool(self._phase)

    def send(self, text: str) -> str | None:
        if self.busy:
            self.failed.emit("上一轮尚未结束")
            return None
        self._phase = "answer"
        self._answer_buf = ""
        self.busy_changed.emit(True)
        try:
            request_id = self.client.prompt(text)
        except RuntimeError as exc:
            self._finish_error(str(exc))
            return None
        self._requests[request_id] = "answer"
        self._answer_request_id = request_id
        self._answer_accepted = False
        self._user_message_seen = False
        return request_id

    def abort(self) -> None:
        if self.busy:
            self.client.abort()

    # ── 引擎事件 ──────────────────────────────────────────────

    def _on_response(self, resp: dict) -> None:
        request_id = str(resp.get("id", ""))
        phase = self._requests.pop(request_id, None)
        if phase is None:
            return
        if resp.get("success"):
            self.request_accepted.emit(request_id)
            if phase == "answer":
                self._answer_accepted = True
                self._maybe_emit_committed()
            return
        error = str(resp.get("error") or "引擎未接受请求")
        self.request_failed.emit(request_id, error)
        if phase == "answer":
            self._answer_request_id = None
        if self._phase == phase:
            self._finish_error(error)

    def _maybe_emit_committed(self) -> None:
        if self._answer_request_id and self._answer_accepted and self._user_message_seen:
            request_id, self._answer_request_id = self._answer_request_id, None
            self.request_committed.emit(request_id)

    def _on_crashed(self, code: int) -> None:
        if not self.busy:
            return
        if self._answer_request_id is not None:
            self.request_failed.emit(self._answer_request_id, f"引擎退出（代码 {code}）")
            self._answer_request_id = None
        self._requests.clear()
        self._finish_error(f"引擎退出（代码 {code}）")

    def _on_event(self, ev: dict) -> None:
        if not self._phase:
            return
        t = ev.get("type")
        if t == "message_end" and self._phase == "answer" and (ev.get("message") or {}).get("role") == "user":
            self._user_message_seen = True
            self._maybe_emit_committed()
        elif t == "message_update" and self._phase == "answer":
            ame = ev.get("assistantMessageEvent") or {}
            if ame.get("type") == "text_delta":
                delta = ame.get("delta", "")
                self._answer_buf += delta
                self.answer_delta.emit(delta)
        elif t == "agent_end":
            if ev.get("willRetry"):
                return
            msgs = ev.get("messages") or []
            stop = ""
            if msgs:
                stop = (msgs[-1].get("stopReason") or "")
            if stop == "error":
                err = (msgs[-1].get("errorMessage") or "引擎错误")
                self._finish_error(err)
                return
            if self._phase == "answer":
                self._end_answer_round(msgs)

    def _final_text(self, msgs: list) -> str:
        """agent_end 携带的完整消息里取最后一条 assistant 文本（比流式 buffer 可靠）。"""
        for m in reversed(msgs):
            if m.get("role") != "assistant":
                continue
            parts = [c.get("text", "") for c in m.get("content", []) if c.get("type") == "text"]
            if parts:
                return "".join(parts)
        return ""

    def _end_answer_round(self, msgs: list) -> None:
        raw = self._final_text(msgs) or self._answer_buf
        result = parse_turn_result(raw)
        self._answer_text = result.detail
        self.answer_done.emit(result.detail)
        self.summarizing.emit()
        self._phase = ""
        self.busy_changed.emit(False)
        self.summary_done.emit(result.brief)
        self.turn_done.emit(result)

    def _finish_error(self, err: str) -> None:
        self._phase = ""
        self.busy_changed.emit(False)
        self.failed.emit(err)


# ── 冒烟自检（对 mock engine 走单回合结果协议）────────────────

if __name__ == "__main__":
    import sys

    from PyQt6.QtCore import QCoreApplication, QTimer

    from .engine_client import EngineClient

    app = QCoreApplication(sys.argv)
    client = EngineClient(mock=True)
    ctrl = ConversationController(client)
    got = {}

    ctrl.answer_done.connect(lambda a: got.update(answer=a[:50]))
    ctrl.summary_done.connect(lambda s: (got.update(summary=s[:50]), app.quit()))
    ctrl.failed.connect(lambda e: (print("FAIL:", e), app.quit()))
    client.start()
    QTimer.singleShot(200, lambda: ctrl.send("自我介绍"))
    QTimer.singleShot(30000, app.quit)
    app.exec()
    client.stop()
    ok = "answer" in got and "summary" in got
    print("SMOKE", "OK" if ok else f"FAIL {got}", got)
    sys.exit(0 if ok else 1)
