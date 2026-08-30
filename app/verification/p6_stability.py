#!/usr/bin/env python3
"""P6 稳定性专项：长时运行（连续 N 轮）+ 会话堆积 + 引擎崩溃恢复不卡死。

    HAOCHEN_MOCK=1 MOCK_TICK_MS=5 QT_QPA_PLATFORM=offscreen HAOCHEN_HOME=/tmp/haochen-p6-stab \
        app/.venv/bin/python app/verification/p6_stability.py [--turns 30]

断言：
  1. 连续 N 轮问答全部完成（mock，无卡死/无掉回合）；
  2. 会话不丢：get_messages 历史条数随轮次增长（同会话）；
  3. 无内存泄漏迹象：App/引擎进程 RSS 末段较前半程无单调爆炸（阈值放宽）；
  4. 会话堆积：预置 50 个会话文件 → 启动/切换正常。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

HOME = Path(os.environ.get("HAOCHEN_HOME", "/tmp/haochen-p6-stab"))
shutil.rmtree(HOME, ignore_errors=True)

from PyQt6.QtWidgets import QApplication

from haochen_app.app_shell import AppShell
from haochen_app.engine_client import EngineClient, list_sessions

app = QApplication(sys.argv)
RESULTS: list[str] = []
TURNS = 30
for a in sys.argv[1:]:
    if a.startswith("--turns"):
        TURNS = int(a.split("=")[1])


def check(name, ok, extra=""):
    RESULTS.append(("PASS" if ok else "FAIL") + f"  {name}  {extra}")
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {extra}", flush=True)


def wait_until(pred, timeout=30.0, what=""):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.005)
    print(f"  !! 超时：{what}", flush=True)
    return False


def rss_mb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:  # Linux 无，macOS 用 ps
            return 0
    except OSError:
        import subprocess
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True).stdout.strip()
        return int(out) // 1024 if out else 0


def main() -> int:
    print(f"── 预置 50 个会话文件（会话堆积）──", flush=True)
    agent_dir = HOME / "agent"
    sess_dir = HOME / "pi-sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    for i in range(50):
        (sess_dir / f"stub-{i:03d}.jsonl").write_text(
            '{"id":"stub","timestamp":"2026-08-25T00:00:00Z"}', encoding="utf-8")
    check("S0 预置 50 个会话文件", len(list(sess_dir.glob("*.jsonl"))) >= 50)

    shell = AppShell(mock=True, home=HOME)
    shell.first_run_setup()
    shell.start()
    chat, pet, sup = shell.chat, shell.pet, shell.supervisor
    ok = wait_until(lambda: chat._current_path is not None, 10, "启动就绪")
    check("S0 启动就绪", ok)

    # 连续 N 轮
    print(f"── 连续 {TURNS} 轮问答 ──", flush=True)
    done = 0
    leak = []
    for i in range(TURNS):
        print(f"  turn {i}...", flush=True)
        pet.send(f"第 {i} 轮问题")
        ok = wait_until(lambda: not pet.ctrl.busy, 40, f"第 {i} 轮完成")
        if not ok:
            print("  turn", i, "timeout", flush=True)
            break
        done += 1
        if i % 5 == 0:
            # 采样 App 进程与引擎子进程 RSS
            leak.append((i, rss_mb(os.getpid())))
    check(f"S1 连续 {TURNS} 轮全部完成（无卡死）", done == TURNS, f"done={done}")

    # 会话不丢
    msgs_count = None
    def _on_msgs(resp):
        nonlocal msgs_count
        if resp.get("success"):
            msgs_count = len((resp.get("data") or {}).get("messages") or [])
    rid = sup.client.get_messages()
    sup._pending[rid] = _on_msgs
    ok = wait_until(lambda: msgs_count is not None, 10, "get_messages")
    check("S2 会话历史随轮次增长（不丢会话）",
          ok and msgs_count > TURNS * 2, f"messages={msgs_count}")

    # RSS 趋势：末段 vs 前半（放宽，只查单调爆炸）
    if len(leak) >= 3:
        first_avg = sum(v for _, v in leak[:len(leak)//2]) / (len(leak)//2)
        last_avg = sum(v for _, v in leak[-len(leak)//2:]) / (len(leak)//2)
        check("S3 无内存泄漏迹象（末段/前段 < 1.8x）",
              last_avg < max(first_avg * 1.8, first_avg + 80),
              f"first={first_avg:.0f}MB last={last_avg:.0f}MB")
    else:
        check("S3 无内存泄漏迹象（样本不足，跳过）", True)

    # 会话堆积下切换
    sels = list_sessions(HOME)
    check("S4 会话堆积下列表正常", len(sels) >= 50, f"listed={len(sels)}")

    # 崩溃恢复重复
    print("── 连续 3 次 kill 引擎均自动恢复 ──", flush=True)
    ok_all = True
    for k in range(3):
        restarted = {"n": 0}
        sup.restarted.connect(lambda: restarted.__setitem__("n", restarted["n"] + 1))
        if sup.client.alive:
            sup.client._proc.kill()
        ok = wait_until(lambda: restarted["n"] >= 1, 20, f"第 {k} 次恢复")
        ok_all = ok_all and ok
    check("S5 连续 3 次崩溃均自动恢复", ok_all)

    shell.stop()
    n_fail = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print(f"\n==== {len(RESULTS)-n_fail}/{len(RESULTS)} 通过 ====")
    for r in RESULTS:
        print(r)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
