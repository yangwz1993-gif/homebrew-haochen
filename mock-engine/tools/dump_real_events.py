#!/usr/bin/env python3
"""真引擎事件 dump 工具（P1 Agent-A 验证证据用）。

spawn 真引擎 --mode rpc --no-extensions，发一条廉价 prompt，
把 stdout 的每一行原始 JSONL 原样落盘，供 rpc-contract.md 对照真实事件形状。

用法:
    python3 dump_real_events.py <haochen-engine 路径> <输出 jsonl 路径> [prompt]

注意: 会真实调用一次模型（廉价 prompt），引擎按 pi 自身行为写 ~/.pi/agent/sessions/。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time

DEFAULT_PROMPT = "Reply with exactly one word: pong"
TIMEOUT_S = 120


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    engine, out_path = sys.argv[1], sys.argv[2]
    prompt = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_PROMPT

    lines: list[str] = []
    done = threading.Event()

    with tempfile.TemporaryDirectory(prefix="haochen-rpc-dump-") as cwd:
        proc = subprocess.Popen(
            [engine, "--mode", "rpc", "--no-extensions"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=cwd,
            bufsize=1,
        )
        assert proc.stdout and proc.stdin

        def reader() -> None:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if line:
                    lines.append(line)
            done.set()

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        proc.stdin.write(json.dumps({"id": "p1", "type": "prompt", "message": prompt}) + "\n")
        proc.stdin.flush()

        # 等到 agent_end 出现（一轮结束），最多 TIMEOUT_S
        deadline = time.time() + TIMEOUT_S
        got_end = False
        while time.time() < deadline:
            for line in lines:
                try:
                    if json.loads(line).get("type") == "agent_end":
                        got_end = True
                        break
                except json.JSONDecodeError:
                    pass
            if got_end:
                break
            time.sleep(0.1)

        time.sleep(1.0)  # 收尾，捞 agent_settled 等尾部事件
        proc.terminate()
        t.join(timeout=5)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    types = {}
    for line in lines:
        try:
            ty = json.loads(line).get("type", "?")
        except json.JSONDecodeError:
            ty = "<non-json>"
        types[ty] = types.get(ty, 0) + 1
    print(f"dumped {len(lines)} lines -> {out_path}")
    print("event counts:", json.dumps(types, ensure_ascii=False, sort_keys=True))
    return 0 if got_end else 1


if __name__ == "__main__":
    sys.exit(main())
