#!/usr/bin/env python3
"""haochen mock 引擎 — 按 docs/rpc-contract.md v2.0 吐出确定性假事件流。

用途：P3 三个 UI agent 无成本联调打桩。stdio JSONL，事件名/字段与真引擎一致
（对照 mock-engine/verification/real-engine-dump*.jsonl 编写）。

场景（按 prompt 关键词确定性触发）：
  - 包含 "读屏" / "屏幕" / "read_screen" → read_screen 工具 + 读屏确认（extension_ui_request）
  - 包含 "错误" / "mock-error"    → 错误回合（stopReason=error + errorMessage）
  - 其它                          → 单回合结果（thinking + 【brief】/【detail】）

会话命令：new_session / switch_session / get_messages / get_state /
set_session_name 均按契约响应；abort 在流式中可打断（stopReason=aborted）。

环境变量：MOCK_TICK_MS（每个流式增量的间隔毫秒，默认 30；设 0 则全速）。
"""
from __future__ import annotations

import json
import os
import select
import sys
import time
import uuid
from pathlib import Path

TICK = max(0, int(os.environ.get("MOCK_TICK_MS", "30"))) / 1000.0
ACCEPT_DELAY = max(0, int(os.environ.get("MOCK_ACCEPT_DELAY_MS", "0"))) / 1000.0
MOCK_MODEL = "mock/mock-v1"
ZERO_USAGE = {
    "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0,
    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
}
FINAL_USAGE = {
    "input": 1024, "output": 96, "cacheRead": 128, "cacheWrite": 0, "reasoning": 24,
    "totalTokens": 1248,
    "cost": {"input": 0.0001, "output": 0.0002, "cacheRead": 0.0, "cacheWrite": 0.0, "total": 0.0003},
}

DENIED_TOOL_TEXT = "用户拒绝了读屏请求（或超时未确认）。请基于已有信息回答，并明确说明没有读屏。"
CONFIRM_TITLE = "haochen 想读屏"
CONFIRM_MESSAGE = ("需要读取当前锁定窗口的屏幕内容才能回答。"
                   "点「读吧」或在输入框回 y 允许；回 n 拒绝。")
FAKE_SCREEN = ("窗口来源：[Safari] Mock 示例页面 — haochen\n"
               "（共 3 段文本、0 张图片）\n\n"
               "这是 mock 引擎返回的屏幕内容。\n页面标题：Mock 示例页面。\n正文：你好，haochen。")


def now_ms() -> int:
    return int(time.time() * 1000)


class _StdinLines:
    """select 安全的行读取器：os.read 进自有缓冲，避免 sys.stdin 预读吞行。

    曾用 select(sys.stdin)+readline：两行同时到达时 readline 预读把第二行
    吞进 stdio 用户态缓冲，select 对 FD 不再报可读 → 后续命令卡死。
    P4 集成（壳同时发两个 get_state）暴露本 bug；真引擎无此问题。
    """

    def __init__(self) -> None:
        self.fd = sys.stdin.fileno()
        self.buf = b""
        self.eof = False

    def readline(self, timeout: float | None) -> str | None:
        """返回一行（不含 \n）；超时返回 None；EOF 返回 'EOF'。"""
        while b"\n" not in self.buf and not self.eof:
            r, _, _ = select.select([self.fd], [], [], timeout)
            if not r:
                return None
            chunk = os.read(self.fd, 65536)
            if chunk == b"":
                self.eof = True
                break
            self.buf += chunk
        if b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            return line.decode("utf-8", "replace")
        if self.eof:
            if self.buf:
                line, self.buf = self.buf, b""
                return line.decode("utf-8", "replace")
            return "EOF"
        return None


class MockEngine:
    def __init__(self) -> None:
        home = os.environ.get("HAOCHEN_HOME")
        self._state_path = Path(home) / "mock-engine-state.json" if home else None
        self.sessions: dict[str, dict] = {}
        self.current = ""
        self._load_state()
        if not self.current or self.current not in self.sessions:
            self.current = self._new_session()
            self._save_state()
        self.aborted = False
        self.inbox: list[dict] = []  # 流式中收到的命令，回合间处理
        self._stdin = _StdinLines()

    # ---------- 基础 IO ----------
    def send(self, obj: dict) -> None:
        try:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            sys.exit(0)  # 壳已关闭 stdout，静默退出

    def respond(self, rid, command: str, data=None, error: str | None = None) -> None:
        msg = {"id": rid, "type": "response", "command": command}
        if error is not None:
            msg["success"] = False
            msg["error"] = error
        else:
            msg["success"] = True
            if data is not None:
                msg["data"] = data
        self.send(msg)

    def read_line(self, timeout: float | None) -> dict | None:
        """读一条 JSON 命令；timeout 秒无输入返回 None；EOF 返回 'EOF'。"""
        raw = self._stdin.readline(timeout)
        if raw is None:
            return None
        if raw == "EOF":
            return "EOF"  # type: ignore[return-value]
        line = raw.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            self.respond(None, "parse", error=f"Failed to parse command: {line[:80]}")
            return None

    def poll_interruptible(self) -> bool:
        """流式中每 tick 调用：处理 abort；其它命令暂存 inbox。返回是否被 abort。"""
        msg = self.read_line(TICK)
        while msg is not None and msg != "EOF":
            t = msg.get("type")
            if t == "abort":
                self.aborted = True
                self.respond(msg.get("id"), "abort")
            else:
                self.inbox.append(msg)
            msg = self.read_line(0)
        return self.aborted

    # ---------- 会话状态 ----------
    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            sessions = data.get("sessions")
            current = data.get("current")
            if isinstance(sessions, dict) and isinstance(current, str):
                self.sessions = sessions
                self.current = current
        except (OSError, ValueError, json.JSONDecodeError):
            self.sessions = {}
            self.current = ""

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._state_path.parent.chmod(0o700)
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"current": self.current, "sessions": self.sessions}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self._state_path)
        self._state_path.chmod(0o600)

    def _new_session(self) -> str:
        sid = uuid.uuid4().hex[:8]
        path = f"mock-session://{sid}.jsonl"
        self.sessions[path] = {"id": sid, "name": None, "history": []}
        return path

    # ---------- 消息构造 ----------
    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": [{"type": "text", "text": text}],
                "timestamp": now_ms()}

    def assistant_message(self, content: list, stop_reason: str = "pending",
                          error_message: str | None = None) -> dict:
        msg = {"role": "assistant", "content": content, "api": "openai-completions",
               "provider": "mock", "model": MOCK_MODEL, "usage": dict(ZERO_USAGE),
               "stopReason": stop_reason, "timestamp": now_ms()}
        if error_message is not None:
            msg["errorMessage"] = error_message
        return msg

    # ---------- 流式助手 ----------
    def stream_thinking(self, text: str, content_index: int = 0) -> None:
        self.send({"type": "message_update", "usage": dict(ZERO_USAGE),
                   "assistantMessageEvent": {"type": "thinking_start",
                                             "contentIndex": content_index}})
        for piece in split_pieces(text):
            if self.poll_interruptible():
                return
            self.send({"type": "message_update", "usage": dict(ZERO_USAGE),
                       "assistantMessageEvent": {"type": "thinking_delta",
                                                 "contentIndex": content_index,
                                                 "delta": piece}})
        self.send({"type": "message_update", "usage": dict(FINAL_USAGE),
                   "assistantMessageEvent": {"type": "thinking_end",
                                             "contentIndex": content_index,
                                             "content": text}})

    def stream_text(self, text: str, content_index: int = 0) -> bool:
        """流式输出正文块。被 abort 时返回 False。"""
        self.send({"type": "message_update", "usage": dict(ZERO_USAGE),
                   "assistantMessageEvent": {"type": "text_start",
                                             "contentIndex": content_index}})
        for piece in split_pieces(text):
            if self.poll_interruptible():
                return False
            self.send({"type": "message_update", "usage": dict(ZERO_USAGE),
                       "assistantMessageEvent": {"type": "text_delta",
                                                 "contentIndex": content_index,
                                                 "delta": piece}})
        self.send({"type": "message_update", "usage": dict(FINAL_USAGE),
                   "assistantMessageEvent": {"type": "text_end",
                                             "contentIndex": content_index,
                                             "content": text}})
        return True

    # ---------- 回合编排 ----------
    def run_prompt(self, rid, message: str) -> None:
        if ACCEPT_DELAY:
            time.sleep(ACCEPT_DELAY)
        self.respond(rid, "prompt")
        self.aborted = False
        self.send({"type": "agent_start"})
        self.send({"type": "turn_start"})

        umsg = self.user_message(message)
        self.send({"type": "message_start", "message": umsg})
        self.send({"type": "message_end", "message": umsg})
        self.current_history().append(umsg)

        if any(k in message for k in ("错误", "mock-error")):
            self._error_turn()
        elif any(k in message for k in ("读屏", "屏幕", "read_screen")):
            self._read_screen_turn()
        else:
            self._plain_turn(message)

        history = list(self.current_history())
        self.send({"type": "agent_end", "messages": history, "willRetry": False})
        self.send({"type": "agent_settled"})

    def _finish_assistant(self, content: list, stop_reason: str = "stop",
                          error_message: str | None = None) -> dict:
        """message_end + turn_end(无工具) + 入历史。返回完整 assistant 消息。"""
        amsg = self.assistant_message(content, stop_reason, error_message)
        if stop_reason in ("stop", "toolUse"):
            amsg["usage"] = dict(FINAL_USAGE)
        self.send({"type": "message_end", "message": amsg})
        self.send({"type": "turn_end", "message": amsg, "toolResults": []})
        self.current_history().append(amsg)
        return amsg

    def _plain_turn(self, question: str) -> None:
        self.send({"type": "message_start",
                   "message": self.assistant_message([])})
        thinking = "用户在问一个普通问题，mock 引擎正在生成简答与详情。"
        self.stream_thinking(thinking, 0)
        answer = ("【brief】\nmock 引擎工作正常。\n"
                  "- 请求已完成，可以直接查看结果。\n"
                  "- 需要更多依据时，请打开详情。\n【/brief】\n"
                  f"【detail】\n这是对「{question}」的 mock 详答。\n\n"
                  "- 本回合走普通对话路径，无工具调用。\n"
                  "- 简答与详情由同一次请求生成。\n\n"
                  "==结论==：单回合结果协议工作正常。\n【/detail】")
        if self.aborted or not self.stream_text(answer, 1):
            partial = [{"type": "thinking", "thinking": thinking},
                       {"type": "text", "text": ""}]
            self._finish_assistant(partial, "aborted")
            return
        self._finish_assistant([{"type": "thinking", "thinking": thinking},
                                {"type": "text", "text": answer}])

    def _error_turn(self) -> None:
        err = "mock 引擎注入的错误：模型服务不可用（演示错误路径）。"
        self.send({"type": "message_start",
                   "message": self.assistant_message([])})
        self.send({"type": "message_update", "usage": dict(ZERO_USAGE),
                   "assistantMessageEvent": {
                       "type": "error", "reason": "error",
                       "error": self.assistant_message([], "error", err)}})
        self._finish_assistant([], "error", err)

    def _read_screen_turn(self) -> None:
        call_id = "mock_call_" + uuid.uuid4().hex[:8]
        args = {"scroll": True, "max_scrolls": 20}
        args_json = json.dumps(args, ensure_ascii=False)

        # turn 1: thinking + toolcall
        self.send({"type": "message_start",
                   "message": self.assistant_message([])})
        thinking = "用户要看屏幕内容，需要调用 read_screen 工具（会先弹确认）。"
        self.stream_thinking(thinking, 0)
        self.send({"type": "message_update", "usage": dict(ZERO_USAGE),
                   "assistantMessageEvent": {"type": "toolcall_start",
                                             "contentIndex": 1, "id": call_id,
                                             "toolName": "read_screen"}})
        for piece in split_pieces(args_json, size=4):
            if self.poll_interruptible():
                break
            self.send({"type": "message_update", "usage": dict(ZERO_USAGE),
                       "assistantMessageEvent": {"type": "toolcall_delta",
                                                 "contentIndex": 1,
                                                 "delta": piece}})
        tool_call = {"type": "toolCall", "id": call_id,
                     "name": "read_screen", "arguments": args}
        self.send({"type": "message_update", "usage": dict(FINAL_USAGE),
                   "assistantMessageEvent": {"type": "toolcall_end",
                                             "contentIndex": 1,
                                             "toolCall": tool_call}})
        amsg1 = self.assistant_message(
            [{"type": "thinking", "thinking": thinking}, tool_call], "toolUse")
        self.send({"type": "message_end", "message": amsg1})
        self.current_history().append(amsg1)

        # 工具执行 + 读屏确认（阻塞等壳答复）
        self.send({"type": "tool_execution_start", "toolCallId": call_id,
                   "toolName": "read_screen", "args": args})
        req_id = str(uuid.uuid4())
        self.send({"type": "extension_ui_request", "id": req_id,
                   "method": "confirm", "title": CONFIRM_TITLE,
                   "message": CONFIRM_MESSAGE, "timeout": 120000})
        confirmed = self._wait_confirm(req_id)

        if confirmed:
            result = {"content": [{"type": "text", "text": FAKE_SCREEN}],
                      "details": {"app": "Safari", "title": "Mock 示例页面 — haochen",
                                  "stats": {"text_blocks": 3, "images": 0,
                                            "images_with_url": 0}}}
            is_error = False
        else:
            result = {"content": [{"type": "text", "text": DENIED_TOOL_TEXT}],
                      "details": {}}
            is_error = True

        self.send({"type": "tool_execution_update", "toolCallId": call_id,
                   "toolName": "read_screen", "args": args,
                   "partialResult": {"content": [], "details": {}}})
        self.send({"type": "tool_execution_end", "toolCallId": call_id,
                   "toolName": "read_screen", "result": result,
                   "isError": is_error})
        tmsg = {"role": "toolResult", "toolCallId": call_id,
                "toolName": "read_screen", "content": result["content"],
                "isError": is_error, "timestamp": now_ms()}
        self.send({"type": "message_start", "message": tmsg})
        self.send({"type": "message_end", "message": tmsg})
        self.current_history().append(tmsg)
        self.send({"type": "turn_end", "message": amsg1, "toolResults": [tmsg]})

        # turn 2: 基于读屏结果给同回合 brief + detail
        self.send({"type": "turn_start"})
        self.send({"type": "message_start",
                   "message": self.assistant_message([])})
        if confirmed:
            answer = ("【brief】\n屏幕读取成功。\n"
                      "- 当前窗口是 Safari 的 Mock 示例页面。\n"
                      "- 页面正文写着「你好，haochen」。\n【/brief】\n"
                      "【detail】\n我读到了你的屏幕（mock）。\n\n"
                      "- 窗口：Safari — Mock 示例页面。\n"
                      "- 内容：页面写着「你好，haochen」。\n\n"
                      "==结论==：读屏确认、授权与工具链路工作正常。\n【/detail】")
        else:
            answer = ("【brief】\n这次没有读取屏幕。\n"
                      "- 你拒绝了读取请求，我看不到当前窗口。\n"
                      "- 需要时重新提问并允许本次读取。\n【/brief】\n"
                      "【detail】\n没读屏，以下基于已有信息。\n\n"
                      "你拒绝了读屏请求，我无法看到当前窗口内容。"
                      "如果你想让我看，重新提问并点「读吧」即可。\n【/detail】")
        if self.aborted or not self.stream_text(answer, 0):
            self._finish_assistant([{"type": "text", "text": ""}], "aborted")
            return
        self._finish_assistant([{"type": "text", "text": answer}])

    def _wait_confirm(self, req_id: str) -> bool:
        """阻塞等 extension_ui_response；超时按拒绝（对齐真引擎 timeout 语义）。"""
        deadline = time.time() + 120
        while time.time() < deadline:
            msg = self.read_line(max(0.0, deadline - time.time()))
            if msg in (None,):
                continue
            if msg == "EOF":
                return False
            if msg.get("type") == "extension_ui_response" and msg.get("id") == req_id:
                if msg.get("cancelled"):
                    return False
                return bool(msg.get("confirmed"))
            if msg.get("type") == "abort":
                self.aborted = True
                self.respond(msg.get("id"), "abort")
                return False
            self.inbox.append(msg)
        return False

    def current_history(self) -> list:
        return self.sessions[self.current]["history"]

    # ---------- 命令分发 ----------
    def handle(self, cmd: dict) -> None:
        rid = cmd.get("id")
        t = cmd.get("type")
        if t == "prompt":
            self.run_prompt(rid, str(cmd.get("message", "")))
        elif t == "abort":
            self.aborted = True
            self.respond(rid, "abort")
        elif t == "new_session":
            self.current = self._new_session()
            self.respond(rid, "new_session", {"cancelled": False})
        elif t == "switch_session":
            path = cmd.get("sessionPath", "")
            if path in self.sessions:
                self.current = path
                self.respond(rid, "switch_session", {"cancelled": False})
            else:
                self.respond(rid, "switch_session",
                             error=f"Session not found: {path}")
        elif t == "get_messages":
            self.respond(rid, "get_messages",
                         {"messages": list(self.current_history())})
        elif t == "get_state":
            sess = self.sessions[self.current]
            self.respond(rid, "get_state", {
                "model": {"id": MOCK_MODEL.split("/", 1)[1], "name": "Mock Model",
                          "api": "openai-completions", "provider": "mock",
                          "baseUrl": "http://localhost/mock", "reasoning": True,
                          "input": ["text"], "cost": {"input": 0, "output": 0},
                          "contextWindow": 128000, "maxTokens": 8192},
                "thinkingLevel": "off", "isStreaming": False,
                "isCompacting": False, "steeringMode": "one-at-a-time",
                "followUpMode": "one-at-a-time", "sessionFile": self.current,
                "sessionId": sess["id"], "sessionName": sess["name"],
                "autoCompactionEnabled": True,
                "messageCount": len(sess["history"]), "pendingMessageCount": 0})
        elif t == "set_session_name":
            name = str(cmd.get("name", "")).strip()
            if not name:
                self.respond(rid, "set_session_name",
                             error="Session name cannot be empty")
            else:
                self.sessions[self.current]["name"] = name
                self.respond(rid, "set_session_name")
        elif t == "extension_ui_response":
            pass  # 未在确认等待中到达的响应，忽略
        else:
            self.respond(rid, str(t), error=f"Unknown command: {t}")
        self._save_state()

    def run(self) -> None:
        while True:
            msg = self.inbox.pop(0) if self.inbox else self.read_line(None)
            if msg == "EOF" or msg is None:
                if msg == "EOF":
                    return
                continue
            self.handle(msg)


def split_pieces(text: str, size: int = 3) -> list[str]:
    """确定性切片：按固定宽度切，模拟流式 delta。"""
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def main() -> None:
    MockEngine().run()


if __name__ == "__main__":
    main()
