"""两步协议编排（answer→summary）+ 解析。P3 三个 UI 模块共用，只读不改。

实现 docs/rpc-contract.md §4.3 的冻结逻辑：
1. answer 回合 agent_end 后解析配对【answer】…【/answer】；
2. 壳踢 summary 回合（冻结模板，haochen-summary-phase 前缀）；
3. summary 回合 agent_end 后解析【summary】，失败则抽取式兜底，绝不死循环再踢。

用法：

    ctrl = ConversationController(client)     # client = EngineClient（已 start）
    ctrl.answer_delta.connect(...)            # str: answer 流式增量（打字机用）
    ctrl.answer_done.connect(...)             # str: 完整详答（已剥标记）
    ctrl.summarizing.connect(...)             # 无参：进入短结阶段（UI 状态文案）
    ctrl.summary_done.connect(...)            # str: 短结（模型解析或兜底抽取）
    ctrl.failed.connect(...)                  # str: 错误描述
    ctrl.send("帮我看看这个页面")              # 发起一轮

注意：`haochen-summary-phase` 开头的消息是协议保留踢令，UI 永不渲染（契约 §4.4）。
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QObject, pyqtSignal

from .engine_client import EngineClient

SUMMARY_KICK_PREFIX = "haochen-summary-phase"
# 配对标记：兼容模型的错误闭合——==answer==/==/answer==、==summary==/==/summary== 等
# 变体视同【】系标记（v0.1.10：真机观测到模型【answer】开头却 ==/answer== 收尾）。
_ANSWER_PAIR = re.compile(r"(?:【answer】|==answer==)\s*(.*?)\s*(?:【/answer】|==/answer==)", re.S)
_SUMMARY_PAIR = re.compile(r"(?:【summary】|==summary==)\s*(.*?)\s*(?:【/summary】|==/summary==)", re.S)
_ANY_TAG = re.compile(r"【/?(?:answer|summary)】|==/?(?:answer|summary)==")

SUMMARY_KICK_TEMPLATE = (
    "haochen-summary-phase\n"
    "下面是详答。请只输出短结：严格【summary】…【/summary】；篇幅 80～160 字；"
    "结构=1 句结论 + 最多 2～3 要点；禁止元评论/括号旁白/大段引用；不要工具、不要再写 answer。\n\n"
    "【answer】\n"
    "{answer}\n"
    "【/answer】"
)


def strip_tags(text: str) -> str:
    return _ANY_TAG.sub("", text or "").strip()


def parse_paired(text: str, kind: str) -> str:
    """解析配对标记块（kind = "answer" | "summary"），无配对返回 ''。"""
    pair = _ANSWER_PAIR if kind == "answer" else _SUMMARY_PAIR
    parts = [p.strip() for p in pair.findall(text or "") if p.strip()]
    return "\n\n".join(parts).strip()


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


class ConversationController(QObject):
    """一轮对话的两步编排。UI 只订阅信号，不碰引擎事件细节。"""

    answer_delta = pyqtSignal(str)    # answer 阶段流式增量
    answer_done = pyqtSignal(str)     # answer 阶段完成（详答全文，已剥标记）
    summarizing = pyqtSignal()        # 进入短结阶段
    summary_done = pyqtSignal(str)    # 短结完成（解析值或兜底抽取）
    failed = pyqtSignal(str)          # 回合错误（stopReason=error 等）
    busy_changed = pyqtSignal(bool)   # 是否在一轮对话中
    request_accepted = pyqtSignal(str)
    request_committed = pyqtSignal(str)
    request_failed = pyqtSignal(str, str)

    def __init__(self, client: EngineClient, parent=None):
        super().__init__(parent)
        self.client = client
        self._phase = ""              # "" | "answer" | "summary"
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
            elif self._phase == "summary":
                self._end_summary_round(msgs)

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
        answer = parse_paired(raw, "answer") or strip_tags(raw)
        self._answer_text = answer
        self.answer_done.emit(answer)
        # 壳踢 summary 回合（冻结模板，截断 8000 字）
        kick = SUMMARY_KICK_TEMPLATE.format(answer=answer[:8000])
        self._phase = "summary"
        self.summarizing.emit()
        try:
            request_id = self.client.prompt(kick)
        except RuntimeError as exc:
            self._finish_error(str(exc))
            return
        self._requests[request_id] = "summary"

    def _end_summary_round(self, msgs: list) -> None:
        raw = self._final_text(msgs)
        summary = parse_paired(raw, "summary") or extractive_summary(self._answer_text)
        self._phase = ""
        self.busy_changed.emit(False)
        self.summary_done.emit(summary)

    def _finish_error(self, err: str) -> None:
        self._phase = ""
        self.busy_changed.emit(False)
        self.failed.emit(err)


# ── 冒烟自检（对 mock engine 走完整两步）──────────────────────

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
    QTimer.singleShot(200, lambda: ctrl.send("自我介绍"))  # mock: 普通对话 → answer；踢令 → summary
    QTimer.singleShot(30000, app.quit)
    app.exec()
    client.stop()
    ok = "answer" in got and "summary" in got
    print("SMOKE", "OK" if ok else f"FAIL {got}", got)
    sys.exit(0 if ok else 1)
