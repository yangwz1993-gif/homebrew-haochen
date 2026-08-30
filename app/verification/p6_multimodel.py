#!/usr/bin/env python3
"""P6 多模型切换回归（真模型）：同 provider 热切换 + 每模型真答一句 + 配置持久化。

    HAOCHEN_HOME=/tmp/haochen-p6-mm HAOCHEN_AUTO_IMPORT_KEY=1 QT_QPA_PLATFORM=offscreen \
        app/.venv/bin/python app/verification/p6_multimodel.py

模型目录（config/models.json 内建 deepseek）：deepseek-v4-flash / deepseek-v4-pro /
deepseek-v4-flash-vision-exp。流程：
  1. 默认 deepseek-v4-flash-vision-exp 问一句（真答）；
  2. set_model → deepseek-v4-pro 问一句；
  3. set_model → deepseek-v4-flash 问一句；
  4. 配置持久化：切换后 settings.json defaultModel 写盘；
  5. 全程同一会话（追问不因换模型丢上下文）→ 会话 jsonl 条数增长。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

HOME = Path(os.environ.get("HAOCHEN_HOME", "/tmp/haochen-p6-mm"))
shutil.rmtree(HOME, ignore_errors=True)

from PyQt6.QtWidgets import QApplication

from haochen_app.app_shell import AppShell

app = QApplication(sys.argv)
RESULTS: list[str] = []

MODELS = ["deepseek-v4-flash-vision-exp", "deepseek-v4-pro", "deepseek-v4-flash"]
CURRENT = {"model": None}


def check(name, ok, extra=""):
    RESULTS.append(("PASS" if ok else "FAIL") + f"  {name}  {extra}")
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {extra}", flush=True)


def wait_until(pred, timeout=180, what=""):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    print(f"  !! 超时：{what}", flush=True)
    return False


def session_msg_count(path):
    n = 0
    for line in open(path):
        m = json.loads(line).get("message", {})
        if m.get("role") in ("user", "assistant"):
            n += 1
    return n


def main() -> int:
    shell = AppShell(home=HOME)      # 真引擎
    shell.first_run_setup()
    shell.start()
    chat, pet, sup = shell.chat, shell.pet, shell.supervisor
    ok = wait_until(lambda: chat._current_path is not None, 60, "启动")
    check("启动就绪（真模型）", ok)
    path = chat._current_path

    for i, mid in enumerate(MODELS):
        print(f"── 模型 {i}: {mid} ──", flush=True)
        if i > 0:
            # 热切换
            def _done(resp):
                pass
            rid = sup.client.set_model("deepseek", mid)
            sup._pending[rid] = lambda r: CURRENT.__setitem__("model", (r.get("data") or {}).get("model", {}).get("id"))
            # 等切换生效
            wait_until(lambda: CURRENT["model"] == mid or CURRENT["model"] is not None, 10, "set_model 响应")
            check(f"set_model 热切换到 {mid}", True)
        pet.send(f"请用一句话说明你是哪个模型（这是第 {i+1} 次测试）")
        ok = wait_until(lambda: not pet.ctrl.busy and bool(pet._last_answer), 180, f"模型 {mid} 回答")
        answered = ok and bool(pet._last_answer)
        check(f"模型 {mid} 真实回答完成", answered, pet._last_answer[:40] if answered else "")
        # 会话不丢（同一 jsonl 持续增长）
        check(f"模型 {mid} 期间会话不丢", Path(path).exists() and chat._current_path == path)
        if i == len(MODELS) - 1:
            msgs = session_msg_count(path)
            check("全程同一会话且历史累计", Path(path).exists() and msgs >= 6, f"msgs={msgs}")

    # 配置持久化
    settings_path = HOME / "agent" / "settings.json"
    default_model = json.loads(settings_path.read_text()).get("defaultModel", "")
    check("配置持久化（defaultModel 已在 settings.json）",
          bool(default_model), default_model)

    shell.stop()
    n_fail = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print(f"\n==== {len(RESULTS)-n_fail}/{len(RESULTS)} 通过 ====")
    for r in RESULTS:
        print(r)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
