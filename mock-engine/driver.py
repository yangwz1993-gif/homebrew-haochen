#!/usr/bin/env python3
"""mock 引擎全流程驱动 + L1 协议断言（docs/rpc-contract.md v2.0）。

用法：
    python3 driver.py                          # 打 mock（默认 ../mock_engine.py）
    python3 driver.py --engine /path/to/haochen-engine --real
                                               # 打真引擎（协议级断言，验事件名/字段）
    python3 driver.py --dump out.jsonl         # 顺手录全部原始事件

对 mock 的断言覆盖契约全流程：
    发 prompt → 收流式 → 收工具确认请求 → 授权 → 同回合收 brief + detail
    外加：错误路径、abort 不测（留给 UI）、多会话（new/get_messages/set_name/switch）。

对真引擎（--real）：只断言协议级不变量（response/agent_start/流式/agent_end 序列与字段），
不断言【brief】【detail】标记 —— 真引擎未加载 haochen 扩展时不会产出标记。

退出码 0 = 全过。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEF_RE = re.compile(r"【brief】([\s\S]*?)【/brief】")
DETAIL_RE = re.compile(r"【detail】([\s\S]*?)【/detail】")

PASS, FAIL = "PASS", "FAIL"


class Driver:
    def __init__(self, argv: list[str], dump_path: str | None = None) -> None:
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.msgs: list[dict] = []
        self.cond = threading.Condition()
        self.dump = open(dump_path, "w", encoding="utf-8") if dump_path else None
        self._eof = False
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            if self.dump:
                self.dump.write(line + "\n")
                self.dump.flush()
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self.cond:
                self.msgs.append(msg)
                self.cond.notify_all()
        with self.cond:
            self._eof = True
            self.cond.notify_all()

    def send(self, payload: dict) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def wait_for(self, pred, start: int = 0, timeout: float = 30.0,
                 desc: str = "") -> dict:
        """在 msgs[start:] 里等第一条满足 pred 的消息；返回 (msg, index)。"""
        deadline = time.time() + timeout
        with self.cond:
            while True:
                for i in range(start, len(self.msgs)):
                    if pred(self.msgs[i]):
                        return self.msgs[i]
                if self._eof:
                    raise TimeoutError(f"engine EOF while waiting: {desc}")
                remain = deadline - time.time()
                if remain <= 0:
                    raise TimeoutError(f"timeout waiting: {desc}")
                self.cond.wait(min(remain, 0.5))

    def close(self) -> None:
        try:
            assert self.proc.stdin
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        if self.dump:
            self.dump.close()


def rid() -> str:
    return uuid.uuid4().hex[:8]


class Checker:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((PASS if ok else FAIL, name, detail))
        print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
        return ok


def round_events(msgs: list[dict], start: int) -> list[dict]:
    return [m for m in msgs[start:] if m.get("type") != "response"]


def assert_round_skeleton(ck: Checker, drv: Driver, start: int, prompt_id: str,
                          label: str, expect_markers: bool) -> tuple[int, str]:
    """等一轮 prompt 跑完（response → agent_end），断言事件骨架。

    返回 (下一轮起点下标, 本轮 assistant 全文)。
    """
    resp = drv.wait_for(
        lambda m: m.get("type") == "response" and m.get("id") == prompt_id,
        start, desc=f"{label}: prompt response")
    ck.check(f"{label}: prompt 受理成功", resp.get("success") is True)

    end_idx_msg = None
    deadline = time.time() + 60
    with drv.cond:
        while end_idx_msg is None:
            for i in range(start, len(drv.msgs)):
                m = drv.msgs[i]
                if m.get("type") == "agent_end" and i > start:
                    end_idx_msg = (i, m)
                    break
            if end_idx_msg is None:
                remain = deadline - time.time()
                if remain <= 0:
                    raise TimeoutError(f"{label}: no agent_end")
                drv.cond.wait(min(remain, 0.5))
    end_i, end = end_idx_msg

    evs = [m for m in drv.msgs[start:end_i + 1] if m.get("type") != "response"]
    # 契约 §3.5：上一轮可能延迟补发 agent_settled，落入本轮窗口，跳过
    while evs and evs[0].get("type") == "agent_settled":
        evs.pop(0)
    types = [m["type"] for m in evs]
    ck.check(f"{label}: agent_start 开头", types[0] == "agent_start", str(types[:3]))
    ck.check(f"{label}: 含 turn_start/turn_end",
             "turn_start" in types and "turn_end" in types)
    ck.check(f"{label}: 含 message_start/message_end",
             "message_start" in types and "message_end" in types)
    ck.check(f"{label}: 有流式 message_update", "message_update" in types)
    text = collect_assistant_text(evs)
    if expect_markers:
        ck.check(f"{label}: 含配对【brief】标记", bool(BRIEF_RE.search(text)))
        ck.check(f"{label}: 含配对【detail】标记", bool(DETAIL_RE.search(text)))
    ck.check(f"{label}: agent_end.willRetry=false",
             end.get("willRetry") is False)
    return end_i + 1, text


def collect_assistant_text(evs: list[dict]) -> str:
    """从 message_end(assistant) 里取权威文本（契约 §3.2）。"""
    out = []
    for m in evs:
        if m.get("type") == "message_end" and m.get("message", {}).get("role") == "assistant":
            for c in m["message"].get("content", []):
                if c.get("type") == "text":
                    out.append(c.get("text", ""))
    return "".join(out)


def run_mock_flow(drv: Driver, ck: Checker) -> bool:
    start = 0

    # ── 场景 1：普通对话，单回合双层结果 ──
    p1 = rid()
    drv.send({"id": p1, "type": "prompt", "message": "你好，请用一句话介绍你自己"})
    start, text = assert_round_skeleton(ck, drv, start, p1, "普通对话", True)
    brief = BRIEF_RE.search(text)
    detail = DETAIL_RE.search(text)
    ck.check("普通对话: brief 可独立显示", bool(brief and brief.group(1).strip()))
    ck.check("普通对话: detail 可展开查看", bool(detail and detail.group(1).strip()))

    # ── 场景 2：读屏确认全流程（门禁主流程）──
    p2 = rid()
    drv.send({"id": p2, "type": "prompt", "message": "看看我的屏幕上有什么"})
    resp2 = drv.wait_for(
        lambda m: m.get("type") == "response" and m.get("id") == p2,
        start, desc="读屏: prompt response")
    ck.check("读屏: prompt 受理成功", resp2.get("success") is True)

    tool_start = drv.wait_for(
        lambda m: m.get("type") == "tool_execution_start"
        and m.get("toolName") == "read_screen", start, desc="读屏: tool start")
    ck.check("读屏: tool_execution_start(read_screen)",
             bool(tool_start.get("toolCallId")))

    req = drv.wait_for(
        lambda m: m.get("type") == "extension_ui_request"
        and m.get("method") == "confirm", start, desc="读屏: confirm 请求")
    ck.check("读屏: confirm 标题/文案符合契约",
             req.get("title") == "haochen 想读屏" and "读吧" in req.get("message", ""))

    drv.send({"type": "extension_ui_response", "id": req["id"], "confirmed": True})

    tool_end = drv.wait_for(
        lambda m: m.get("type") == "tool_execution_end"
        and m.get("toolName") == "read_screen", start, desc="读屏: tool end")
    ck.check("读屏: 授权后工具成功（isError=false）",
             tool_end.get("isError") is False)
    ck.check("读屏: 工具结果含屏幕文本",
             "屏幕" in json.dumps(tool_end.get("result", {}), ensure_ascii=False))

    start, text2 = assert_round_skeleton_tail(ck, drv, start, p2, "读屏")
    brief2 = BRIEF_RE.search(text2)
    detail2 = DETAIL_RE.search(text2)
    ck.check("读屏: 含配对【brief】标记", bool(brief2))
    ck.check("读屏: 含配对【detail】标记", bool(detail2))

    # ── 场景 3：错误路径 ──
    p3 = rid()
    drv.send({"id": p3, "type": "prompt", "message": "触发错误"})
    drv.wait_for(lambda m: m.get("type") == "response" and m.get("id") == p3,
                 start, desc="错误: response")
    err_end = drv.wait_for(
        lambda m: m.get("type") == "message_end"
        and m.get("message", {}).get("role") == "assistant"
        and m["message"].get("stopReason") == "error",
        start, timeout=30, desc="错误: stopReason=error")
    ck.check("错误: assistant stopReason=error + errorMessage",
             bool(err_end["message"].get("errorMessage")),
             err_end["message"].get("errorMessage", "")[:40])
    end3 = drv.wait_for(lambda m: m.get("type") == "agent_end", start,
                        desc="错误: agent_end")
    ck.check("错误: agent_end 正常收尾", end3.get("willRetry") is False)
    start = drv.msgs.index(end3) + 1

    # ── 场景 4：多会话 ──
    g0 = rid()
    drv.send({"id": g0, "type": "get_state"})
    st0 = drv.wait_for(lambda m: m.get("type") == "response" and m.get("id") == g0,
                       start, desc="会话: get_state")
    sess0 = st0["data"]["sessionFile"]
    ck.check("会话: get_state 返回 sessionFile/sessionId",
             bool(sess0) and bool(st0["data"].get("sessionId")))

    n1 = rid()
    drv.send({"id": n1, "type": "new_session"})
    rn = drv.wait_for(lambda m: m.get("type") == "response" and m.get("id") == n1,
                      start, desc="会话: new_session")
    ck.check("会话: new_session 成功",
             rn.get("success") is True
             and rn.get("data", {}).get("cancelled") is False)

    p4 = rid()
    drv.send({"id": p4, "type": "prompt", "message": "这是第二个会话"})
    start, _ = assert_round_skeleton(ck, drv, start, p4, "第二会话对话", True)

    gm = rid()
    drv.send({"id": gm, "type": "get_messages"})
    rgm = drv.wait_for(lambda m: m.get("type") == "response" and m.get("id") == gm,
                       start, desc="会话: get_messages")
    msgs = rgm.get("data", {}).get("messages", [])
    ck.check("会话: get_messages 返回本会话历史", len(msgs) >= 2,
             f"{len(msgs)} 条")

    sn = rid()
    drv.send({"id": sn, "type": "set_session_name", "name": "mock 测试会话"})
    rsn = drv.wait_for(lambda m: m.get("type") == "response" and m.get("id") == sn,
                       start, desc="会话: set_session_name")
    ck.check("会话: set_session_name 成功", rsn.get("success") is True)

    sw = rid()
    drv.send({"id": sw, "type": "switch_session", "sessionPath": sess0})
    rsw = drv.wait_for(lambda m: m.get("type") == "response" and m.get("id") == sw,
                       start, desc="会话: switch_session")
    ck.check("会话: switch_session 切回成功", rsw.get("success") is True)

    swb = rid()
    drv.send({"id": swb, "type": "switch_session", "sessionPath": "mock-session://nope"})
    rswb = drv.wait_for(lambda m: m.get("type") == "response" and m.get("id") == swb,
                        start, desc="会话: switch 不存在")
    ck.check("会话: switch 不存在的会话报错",
             rswb.get("success") is False and bool(rswb.get("error")))

    unk = rid()
    drv.send({"id": unk, "type": "bogus_command"})
    rk = drv.wait_for(lambda m: m.get("type") == "response" and m.get("id") == unk,
                      start, desc="未知命令")
    ck.check("未知命令: success=false + error",
             rk.get("success") is False and "Unknown command" in rk.get("error", ""))

    return all(r[0] == PASS for r in ck.results)


def assert_round_skeleton_tail(ck: Checker, drv: Driver, start: int,
                               prompt_id: str, label: str) -> tuple[int, str]:
    """工具回合已在调用方消费过部分事件，这里只等 agent_end 并收集文本。"""
    end = drv.wait_for(lambda m: m.get("type") == "agent_end", start,
                       timeout=60, desc=f"{label}: agent_end")
    end_i = drv.msgs.index(end)
    evs = [m for m in drv.msgs[start:end_i + 1] if m.get("type") != "response"]
    # 契约 §3.5：上一轮可能延迟补发 agent_settled，落入本轮窗口，跳过
    while evs and evs[0].get("type") == "agent_settled":
        evs.pop(0)
    types = [m["type"] for m in evs]
    ck.check(f"{label}: agent_start 开头", types[0] == "agent_start")
    ck.check(f"{label}: agent_end.willRetry=false", end.get("willRetry") is False)
    return end_i + 1, collect_assistant_text(evs)


def run_real_protocol(drv: Driver, ck: Checker) -> bool:
    """对真引擎的协议级断言（廉价 prompt，一轮）。"""
    p1 = rid()
    drv.send({"id": p1, "type": "prompt",
              "message": "Reply with exactly one word: pong"})
    start, text = assert_round_skeleton(ck, drv, 0, p1, "真引擎普通对话", False)
    ck.check("真引擎: assistant 文本非空", bool(text.strip()), text.strip()[:40])
    # message_update 字段形状
    updates = [m for m in drv.msgs[:start]
               if m.get("type") == "message_update"]
    ck.check("真引擎: message_update 含 usage + assistantMessageEvent",
             all("usage" in m and "assistantMessageEvent" in m for m in updates)
             and len(updates) > 0, f"{len(updates)} 条")
    if updates:
        ev = updates[0]["assistantMessageEvent"]
        ck.check("真引擎: assistantMessageEvent 无 partial 字段（线上剥形）",
                 "partial" not in ev and "type" in ev)
    return all(r[0] == PASS for r in ck.results)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", help="引擎可执行文件路径（默认打 mock）")
    ap.add_argument("--real", action="store_true",
                    help="目标是真引擎：只跑协议级断言")
    ap.add_argument("--dump", help="录制全部 stdout JSONL 到该文件")
    args = ap.parse_args()

    if args.engine:
        argv = [os.path.abspath(args.engine), "--mode", "rpc", "--no-extensions"]
    else:
        argv = [sys.executable, os.path.join(HERE, "mock_engine.py")]

    ck = Checker()
    drv = Driver(argv, args.dump)
    try:
        ok = run_real_protocol(drv, ck) if args.real else run_mock_flow(drv, ck)
    except (TimeoutError, KeyError, AssertionError) as e:
        ck.check("流程异常中断", False, str(e))
        ok = False
    finally:
        drv.close()

    passed = sum(1 for r in ck.results if r[0] == PASS)
    print(f"\n==== {passed}/{len(ck.results)} 通过 ====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
