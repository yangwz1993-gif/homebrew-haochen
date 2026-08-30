#!/usr/bin/env python3
"""P4 集成验证：单 App 壳 + 唯一引擎 + 双入口同会话 + 崩溃恢复 + 配置生效链。

用法（仓库根目录）：
    HAOCHEN_MOCK=1 MOCK_TICK_MS=30 QT_QPA_PLATFORM=offscreen \
    HAOCHEN_HOME=/tmp/haochen-p4-test HAOCHEN_AUTO_IMPORT_KEY=1 HAOCHEN_AUTO_RESTART=1 \
        app/.venv/bin/python app/verification/p4_integration.py

产物：app/verification/p4-*.png + 断言全 PASS。
场景对应 P4 门禁（开发总纲 §二）：单一入口/进程管理/双入口同会话/真链路前的 mock 全链路。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]           # app/
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

HOME = Path(os.environ.get("HAOCHEN_HOME", "/tmp/haochen-p4-test"))
shutil.rmtree(HOME, ignore_errors=True)                  # 干净首启

from PyQt6.QtWidgets import QApplication

from haochen_app.app_shell import AppShell
from haochen_app.chat.widgets import StatusBubble, UserBubble
from haochen_app.supervisor import EngineSupervisor

app = QApplication(sys.argv)
RESULTS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append(("PASS" if ok else "FAIL") + f"  {name}  {extra}")
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {extra}", flush=True)


def pump(seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(pred, timeout: float = 30.0, what: str = "") -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    print(f"  !! 超时：{what}", flush=True)
    return False


def flow_texts(win, cls) -> list[str]:
    """收集对话流里某类气泡的纯文本（MarkdownView → toPlainText）。"""
    out = []
    for i in range(win.flow.count()):
        w = win.flow.itemAt(i).widget()
        if w is not None and hasattr(w, "content") and isinstance(w.content, cls):
            view = getattr(w.content, "view", None)
            out.append(view.toPlainText() if view is not None else "")
    return out


def main() -> int:
    # ── S0 装配 + 首启 ─────────────────────────────────────────
    print("── S0 装配：唯一引擎 + 首启引导 ──", flush=True)
    shell = AppShell(mock=True, home=HOME)                 # mock 走 HAOCHEN_MOCK/参数
    shell.first_run_setup()
    shell.start()
    chat, pet, sup = shell.chat, shell.pet, shell.supervisor

    check("S0 三 UI 共享同一 EngineClient",
          chat.client is pet.client is sup.client)
    ok = wait_until(lambda: sup.client.alive and chat._current_path is not None,
                    15, "引擎启动 + get_state")
    check("S0 引擎单进程启动并就绪", ok and chat.client._proc is pet.client._proc)

    imported_key = shell.store.get_key("deepseek") or ""
    check("S0 首启 key 导入（占位→真 key，只读 ~/.pi）",
          bool(imported_key) and not imported_key.startswith("sk-在此填入"),
          "(无 ~/.pi key 时本断言为环境依赖)")

    # ── S1 气泡入口发起回合 ────────────────────────────────────
    print("── S1 气泡提问（窗口隐藏）──", flush=True)
    session_at_start = chat._current_path
    pet.send("自我介绍")
    ok = wait_until(lambda: not pet.ctrl.busy and pet.state.value == "AWAKE",
                    30, "气泡回合完成")
    check("S1 气泡两步回合完成（短结已出）", ok)

    # ── S2 镜像：窗口可见即同步气泡历史 ────────────────────────
    print("── S2 打开对话窗口 → 镜像同一会话 ──", flush=True)
    shell.show_chat()
    ok = wait_until(lambda: any("自我介绍" in t for t in flow_texts(chat, UserBubble)),
                    15, "窗口镜像气泡历史")
    check("S2 窗口镜像到气泡的提问", ok)
    check("S2 双入口同一会话路径", chat._current_path == session_at_start,
          chat._current_path or "")

    # ── S3 窗口追问 → 会话不丢 ─────────────────────────────────
    print("── S3 窗口追问（同会话、不丢上下文）──", flush=True)
    chat.input.setPlainText("窗口里的追问")
    chat._on_send()
    ok = wait_until(lambda: any("窗口里的追问" in t for t in flow_texts(chat, UserBubble))
                    and not chat.ctrl.busy, 30, "窗口追问回合")
    check("S3 窗口追问完成", ok)
    check("S3 追问仍在同一会话", chat._current_path == session_at_start)
    check("S3 气泡侧未抢显窗口的回合", pet._last_user_text != "窗口里的追问")
    chat.grab().save(str(OUT / "p4-01-chat-mirrored.png"))

    # ── S4 确认路由：只到发起方 ────────────────────────────────
    print("── S4 读屏确认路由（气泡发起→气泡确认）──", flush=True)
    pet.send("看看我的屏幕上有什么")
    ok = wait_until(lambda: pet._confirm_id is not None, 20, "气泡确认条")
    check("S4 确认条路由到气泡（发起方）", ok)
    check("S4 对话窗口未重复弹确认", chat._confirm is None)
    pet._resolve_confirm(confirmed=True)
    ok = wait_until(lambda: not pet.ctrl.busy, 30, "读屏回合完成")
    check("S4 气泡授权后两步完成", ok)
    pet.bubble.grab().save(str(OUT / "p4-02-bubble-after-readscreen.png"))
    pet.pet.grab().save(str(OUT / "p4-03-pet-idle.png"))

    print("── S4b 读屏确认路由（窗口发起→窗口确认）──", flush=True)
    chat.input.setPlainText("看看我的屏幕上有什么")
    chat._on_send()
    ok = wait_until(lambda: chat._confirm is not None, 20, "窗口确认条")
    check("S4b 确认条路由到窗口（发起方）", ok)
    check("S4b 气泡未重复弹确认", pet._confirm_id is None)
    chat._answer_confirm(True)
    ok = wait_until(lambda: not chat.ctrl.busy, 30, "窗口读屏回合完成")
    check("S4b 窗口授权后两步完成", ok)

    # ── S5 并发仲裁：气泡生成中，窗口输入排队 ──────────────────
    print("── S5 并发仲裁（不双发引擎）──", flush=True)
    pet.send("自我介绍")
    busy_seen = wait_until(lambda: pet.ctrl.busy, 5, "气泡回合进行中")
    chat.input.setPlainText("排队的问题")
    chat._on_send()
    check("S5 窗口消息在气泡回合中排队（未并发打引擎）",
          busy_seen and "排队的问题" in chat._queue)
    ok = wait_until(lambda: not chat._queue and not chat.ctrl.busy and not pet.ctrl.busy,
                    40, "排队泄流")
    check("S5 气泡回合结束后窗口排队自动泄流", ok)
    check("S5 全程同一会话", chat._current_path == session_at_start)

    print("── S5b 反向并发仲裁（窗口生成中，气泡输入排队）──", flush=True)
    chat.input.setPlainText("窗口先发送")
    chat._on_send()
    chat_busy = wait_until(lambda: chat.ctrl.busy, 5, "窗口回合进行中")
    pet.send("气泡排队的问题")
    check("S5b 气泡消息在窗口回合中进入共享队列",
          chat_busy and "气泡排队的问题" in sup.coordinator.texts("pet"))
    ok = wait_until(
        lambda: not sup.coordinator.queue and not chat.ctrl.busy and not pet.ctrl.busy,
        40,
        "反向排队泄流",
    )
    check("S5b 窗口回合结束后气泡队列自动泄流", ok)

    # ── S6 崩溃 → 自动重启 → 会话恢复 ──────────────────────────
    print("── S6 引擎崩溃 → 自动重启 → 会话恢复 ──", flush=True)
    path_before = chat._current_path
    restarted = {"n": 0}
    sup.restarted.connect(lambda: restarted.__setitem__("n", restarted["n"] + 1))
    sup.client._proc.kill()
    ok = wait_until(lambda: restarted["n"] >= 1, 20, "自动重启完成")
    check("S6 崩溃后自动重启成功", ok)
    # mock 会话现也持久化：验证恢复结果，而非只验证发过 switch_session。
    restored_path = sup.coordinator.current_session
    check("S6 重启后恢复崩溃前会话", restored_path == path_before,
          f"target={path_before} actual={restored_path}")
    ok = wait_until(lambda: chat._current_path is not None, 10, "窗口状态刷新")
    check("S6 窗口崩溃标记已清除", ok and not chat._engine_crashed)
    pet.send("自我介绍")
    ok = wait_until(lambda: not pet.ctrl.busy, 30, "重启后气泡再问答")
    check("S6 重启后引擎可正常问答", ok)
    chat.grab().save(str(OUT / "p4-04-chat-after-restart.png"))

    # ── S7 配置生效链 ──────────────────────────────────────────
    print("── S7 配置 → 引擎生效链 ──", flush=True)
    with patch.object(sup.client, "set_model") as spy:
        shell._on_model_changed("deepseek", "deepseek-v4-flash-vision-exp")
        check("S7 同 provider 换模型 → set_model 热切换", spy.call_count == 1)
    with patch.object(sup, "restart_now") as spy_restart:
        shell._on_restart_required("测试：provider 已切换")  # AUTO_RESTART=1
        check("S7 需重启的配置 → 触发引擎重启", spy_restart.call_count == 1)

    # ── S8 重启失败兜底（坏引擎路径，3 次放弃）─────────────────
    print("── S8 引擎连续重启失败 → 明确报错 ──", flush=True)
    sup2 = EngineSupervisor(mock=False, engine=Path("/nonexistent-engine"), home=HOME)
    sup2.MAX_ATTEMPTS = 2
    sup2.BACKOFF_MS = [10, 20]
    failed = {"n": 0}
    sup2.restart_failed.connect(lambda: failed.__setitem__("n", failed["n"] + 1))
    sup2.start()
    ok = wait_until(lambda: failed["n"] >= 1, 15, "restart_failed")
    check("S8 连续失败 → restart_failed（不白屏、给手动入口）", ok)
    sup2.stop()

    # ── 汇总 ──────────────────────────────────────────────────
    shell.settings.grab().save(str(OUT / "p4-05-settings.png"))
    shell.stop()
    n_fail = sum(1 for r in RESULTS if r.startswith("FAIL"))
    print(f"\n==== {len(RESULTS) - n_fail}/{len(RESULTS)} 通过 ====")
    for r in RESULTS:
        print(r)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
