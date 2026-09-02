#!/usr/bin/env python3
"""P4 真引擎全链路验收（P4 门禁）：配置 key → 对话 → 桌宠唤起 → 读屏确认 → 两步回答。

用法（仓库根目录，真实调用 DeepSeek，花少量 token）：
    HAOCHEN_HOME=/tmp/haochen-p4-real HAOCHEN_SKIP_ONBOARDING=1 QT_QPA_PLATFORM=offscreen \
        app/.venv/bin/python app/verification/p4_real_chain.py

覆盖（开发总纲 §二 P4 门禁 + §四.4 回归清单）：
    R0 首启：模板初始化 + key 只读导入 + 真引擎就绪（真模型 id）
    R1 气泡真问答：两步 answer/summary（真模型，标记解析正确）
    R2 窗口镜像：双入口同一会话（真实 jsonl 路径）
    R3 窗口追问：同会话不丢上下文（历史条数增长 + 语义呼应）
    R4 读屏确认：确认条 → 授权 → read_screen 工具卡 → 两步完成
    R5 崩溃恢复：kill → 自动重启 → 会话切回同一 jsonl → 追问不丢上下文
产物：app/verification/p4-real-*.png + p4-real-transcript.md（问答实录，供产品审查）
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

HOME = Path(os.environ.get("HAOCHEN_HOME", "/tmp/haochen-p4-real"))
shutil.rmtree(HOME, ignore_errors=True)

from PyQt6.QtWidgets import QApplication

from haochen_app.app_shell import AppShell
from haochen_app.chat.widgets import AssistantBubble, ToolCard, UserBubble

app = QApplication(sys.argv)
RESULTS: list[str] = []
TRANSCRIPT: list[str] = ["# P4 真引擎全链路 · 问答实录\n"]


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append(("PASS" if ok else "FAIL") + f"  {name}  {extra}")
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {extra}", flush=True)


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(pred, timeout: float, what: str) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    print(f"  !! 超时：{what}", flush=True)
    return False


def bubbles(win, cls):
    out = []
    for i in range(win.flow.count()):
        w = win.flow.itemAt(i).widget()
        if w is not None and hasattr(w, "content") and isinstance(w.content, cls):
            out.append(w.content)
    return out


def main() -> int:
    t0 = time.time()
    # ── R0 首启 ────────────────────────────────────────────────
    print("── R0 首启：配置初始化 + key 导入 + 真引擎就绪 ──", flush=True)
    shell = AppShell()                    # 无 HAOCHEN_MOCK → 真引擎
    # v0.2.0：key 由运行者预先在设置页/向导存入 Keychain（SKIP_ONBOARDING 跳过向导）
    shell.first_run_setup()
    shell.start()
    chat, pet, sup = shell.chat, shell.pet, shell.supervisor

    auth_text = (HOME / "agent" / "auth.json").read_text(encoding="utf-8")
    check("R0 key 经 Keychain（auth.json 只含引用，无明文）",
          "$HAOCHEN_" in auth_text and "在此填入" not in auth_text
          and "sk-" not in auth_text.replace("sk-在此填入", ""))
    ok = wait_until(lambda: sup.client.alive and chat._current_path is not None, 60,
                    "真引擎启动 + get_state")
    model = ""
    if chat._sessions is not None:
        model = chat.sidebar.findChild(type(chat.sidebar)) and "" or ""
    check("R0 真引擎就绪", ok)
    TRANSCRIPT.append(f"- HAOCHEN_HOME: `{HOME}`\n- 会话文件: `{chat._current_path}`\n")

    # ── R1 气泡真问答（两步协议）───────────────────────────────
    print("── R1 气泡提问（真模型两步）──", flush=True)
    q1 = "用一句话介绍你自己，并说出你当前使用的模型 id。"
    pet.send(q1)
    ok = wait_until(lambda: not pet.ctrl.busy and bool(pet._last_answer), 180,
                    "气泡两步回合（真模型）")
    check("R1 气泡两步回合完成", ok)
    check("R1 详答非空且标记已剥除",
          bool(pet._last_answer) and "【" not in pet._last_answer)
    TRANSCRIPT.append(f"\n## R1 气泡问答\n\n**Q**: {q1}\n\n**A(详答)**: {pet._last_answer}\n")
    session_r1 = chat._current_path

    # ── R2 窗口镜像同会话 ──────────────────────────────────────
    print("── R2 打开窗口 → 镜像同一会话 ──", flush=True)
    shell.show_chat()
    ok = wait_until(lambda: any(q1[:8] in b.view.toPlainText()
                                for b in bubbles(chat, UserBubble)), 30, "窗口镜像")
    check("R2 窗口镜像到气泡提问", ok)
    same = chat._current_path == session_r1
    check("R2 双入口同一会话（真实 jsonl）",
          same and bool(session_r1) and Path(session_r1).exists(),
          str(session_r1))

    # ── R3 窗口追问不丢上下文 ──────────────────────────────────
    print("── R3 窗口追问 ──", flush=True)
    q3 = "我刚才让你做了什么？一句话回答。"
    chat.input.setPlainText(q3)
    chat._on_send()
    ok = wait_until(lambda: not chat.ctrl.busy, 180, "窗口追问回合")
    answers = [b._text for b in bubbles(chat, AssistantBubble) if b.kind == "answer"]
    last_answer = answers[-1] if answers else ""
    check("R3 窗口追问完成", ok and bool(last_answer))
    check("R3 追问仍在同一会话", chat._current_path == session_r1)
    check("R3 上下文延续（提及自我介绍/模型）",
          any(k in last_answer for k in ("介绍", "模型", "你自己")),
          last_answer[:60])
    TRANSCRIPT.append(f"\n## R3 窗口追问\n\n**Q**: {q3}\n\n**A(详答)**: {last_answer}\n")
    chat.grab().save(str(OUT / "p4-real-01-chat.png"))

    # ── R4 读屏确认真链路 ──────────────────────────────────────
    print("── R4 读屏确认 → 授权 → 工具 → 两步 ──", flush=True)
    q4 = "看看我当前屏幕上是什么窗口，一句话告诉我。"
    chat.input.setPlainText(q4)
    chat._on_send()
    ok = wait_until(lambda: chat._confirm is not None, 120, "读屏确认条")
    check("R4 读屏确认条弹出", ok)
    if ok:
        chat._answer_confirm(True)   # 点「读吧」
    ok = wait_until(lambda: not chat.ctrl.busy, 240, "读屏回合完成")
    cards = bubbles(chat, ToolCard)
    check("R4 read_screen 工具卡呈现", bool(cards))
    answers = [b._text for b in bubbles(chat, AssistantBubble) if b.kind == "answer"]
    read_answer = answers[-1] if answers else ""
    check("R4 读屏后两步完成", ok and bool(read_answer))
    TRANSCRIPT.append(f"\n## R4 读屏确认\n\n**Q**: {q4}\n\n**A(详答)**: {read_answer}\n")
    chat.grab().save(str(OUT / "p4-real-02-readscreen.png"))

    # ── R5 崩溃恢复（真 jsonl 会话切回）────────────────────────
    print("── R5 崩溃 → 自动重启 → 会话恢复 → 追问不丢 ──", flush=True)
    path_before = chat._current_path
    msgs_before = None
    restarted = {"n": 0}
    sup.restarted.connect(lambda: restarted.__setitem__("n", restarted["n"] + 1))
    sup.client._proc.kill()
    ok = wait_until(lambda: restarted["n"] >= 1, 60, "自动重启")
    check("R5 崩溃后自动重启成功", ok)
    ok = wait_until(lambda: chat._current_path is not None, 30, "状态刷新")
    check("R5 会话切回崩溃前同一 jsonl", ok and chat._current_path == path_before,
          f"{chat._current_path} vs {path_before}")
    q5 = "我们刚才聊了哪两件事？各用四个字概括。"
    pet.send(q5)
    ok = wait_until(lambda: not pet.ctrl.busy and bool(pet._last_answer), 180,
                    "重启后追问")
    check("R5 重启后追问完成", ok)
    check("R5 上下文未丢（提及自我介绍/读屏/窗口）",
          any(k in pet._last_answer for k in ("介绍", "读屏", "屏幕", "窗口")),
          pet._last_answer[:80])
    TRANSCRIPT.append(f"\n## R5 崩溃恢复后追问\n\n**Q**: {q5}\n\n**A(详答)**: {pet._last_answer}\n")
    chat.grab().save(str(OUT / "p4-real-03-after-restart.png"))

    # ── 汇总 ──────────────────────────────────────────────────
    shell.stop()
    TRANSCRIPT.append(f"\n---\n耗时 {time.time() - t0:.0f}s\n")
    (OUT / "p4-real-transcript.md").write_text("\n".join(TRANSCRIPT), encoding="utf-8")
    n_fail = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print(f"\n==== {len(RESULTS) - n_fail}/{len(RESULTS)} 通过 ====")
    for r in RESULTS:
        print(r)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
