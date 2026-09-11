#!/usr/bin/env python3
"""M-C 对话前端场景验证：对 mock 引擎驱动窗口，逐场景 Qt grab 截图。

用法（仓库根目录）：
    HAOCHEN_MOCK=1 MOCK_TICK_MS=5 QT_QPA_PLATFORM=offscreen \
        app/.venv/bin/python app/haochen_app/chat/verification/run_scenarios.py

产物：同目录 01-*.png … 08-*.png（验证什么见 README.md）。
v0.1.7 新增断言：用户气泡浅绿 / bot 气泡与工具卡清晰框线 / 确认条回车=读吧。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[3]          # app/
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
os.environ.setdefault("HAOCHEN_HOME", tempfile.mkdtemp(prefix="haochen-chat-verification-"))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from haochen_app.chat import ChatWindow
from haochen_app.chat.widgets import AssistantBubble, ConfirmBar, ToolCard, UserBubble

app = QApplication(sys.argv)
from haochen_app.chat.theme import C, app_stylesheet
app.setStyleSheet(app_stylesheet())


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
    print(f"  !! 超时：{what}")
    return False


def grab(win: ChatWindow, name: str) -> None:
    pump(0.3)
    win.grab().save(str(OUT / name))
    print(f"  ✓ {name}")


def send(win: ChatWindow, text: str) -> None:
    win.input.setPlainText(text)
    win._on_send()


def find_confirm_bar(win: ChatWindow) -> ConfirmBar | None:
    return win.findChild(ConfirmBar)


def main() -> int:
    win = ChatWindow()          # HAOCHEN_MOCK=1 → mock 引擎
    win.start()
    win.show()
    assert wait_until(lambda: win._current_path is not None, 10, "get_state")
    print(f"当前会话: {win._current_path}")
    pump(0.5)   # 等首启 get_messages 历史渲染落定，再发第一条（否则历史重渲染会冲掉新用户气泡）

    # ── 场景 1：普通对话（打字机 → 详答 → 结论先行短结 + Markdown）─────────
    print("场景1 普通对话")
    send(win, "自我介绍")
    ok = wait_until(lambda: not win.ctrl.busy, 30, "第一轮结束")
    grab(win, "01-normal-conversation.png")
    assert ok
    # v0.1.7：详情页用户气泡浅绿（同小气泡），bot 气泡加回清晰框线
    ub = win.findChild(UserBubble)
    ab = win.findChild(AssistantBubble)
    assert ub is not None and C["accent_tint"] in ub.styleSheet(), "用户气泡应使用当前主题 accent_tint"
    assert ab is not None and "border: 1px solid" in ab.styleSheet(), "bot 气泡应有清晰框线"
    print("  ✓ 用户气泡浅绿 / bot 气泡有框线")

    # ── 场景 2：读屏确认（确认条 → 授权 → 工具卡 ✓）────────────────────────
    print("场景2 读屏确认")
    send(win, "看看我的屏幕上有什么")
    ok = wait_until(lambda: find_confirm_bar(win) is not None, 30, "确认条出现")
    grab(win, "02-read-screen-confirm.png")
    assert ok
    find_confirm_bar(win).btn_yes.click()   # 点「读吧」
    ok = wait_until(lambda: not win.ctrl.busy, 30, "读屏回合结束")
    grab(win, "03-read-screen-allowed.png")
    assert ok
    card = win.findChild(ToolCard)
    assert card is not None, "工具卡未出现"
    assert "1px solid" in card.styleSheet(), "工具卡应有清晰描边（v0.1.7）"
    card.toggle.setChecked(True)            # 展开工具卡看 args/输出
    grab(win, "04-tool-card-expanded.png")

    # ── 场景 2b：读屏确认回车 = 「读吧」（v0.1.7）───────────────────────────
    print("场景2b 读屏确认回车")
    send(win, "再看看我的屏幕")
    ok = wait_until(lambda: find_confirm_bar(win) is not None, 30, "确认条出现")
    assert ok
    grab(win, "04b-read-screen-enter-confirm.png")
    QTest.keyClick(find_confirm_bar(win), Qt.Key.Key_Return)  # 回车确认
    pump(0.2)
    assert win._confirm is None, "回车应确认并清掉确认条"
    ok = wait_until(lambda: not win.ctrl.busy, 30, "回车确认后回合结束")
    assert ok

    # ── 场景 3：错误路径（错误条 + 重试入口）────────────────────────────────
    print("场景3 错误路径")
    send(win, "演示一个错误")
    ok = wait_until(lambda: not win.ctrl.busy, 30, "错误回合结束")
    grab(win, "05-error-retry.png")
    assert ok

    # ── 场景 4：多会话（新建 → 聊 → 切回 → 历史渲染）────────────────────────
    print("场景4 多会话切换")
    win.sidebar.new_requested.emit()
    ok = wait_until(lambda: len(win._sessions) >= 2 and not win.ctrl.busy, 10, "新会话")
    assert ok
    send(win, "第二个会话里的问题")
    ok = wait_until(lambda: not win.ctrl.busy, 30, "第二会话回合")
    assert ok
    first = win._sessions[-1]["path"]       # 列表新→旧，最旧的是第一个会话
    win.sidebar.session_selected.emit(first)
    ok = wait_until(lambda: win._current_path == first, 10, "切回会话1")
    pump(0.5)
    grab(win, "06-multi-session-switch.png")
    assert ok

    # ── 场景 5：引擎崩溃 → 明确报错 → 重启恢复 ─────────────────────────────
    print("场景5 崩溃恢复")
    win.client._proc.kill()                 # 模拟引擎被杀
    ok = wait_until(lambda: win._engine_crashed, 10, "崩溃感知")
    grab(win, "07-engine-crashed.png")
    assert ok
    win._restart_engine()
    ok = wait_until(lambda: win._current_path is not None and not win._engine_crashed,
                    10, "重启恢复")
    grab(win, "08-engine-restarted.png")
    assert ok

    win.client.stop()
    print("全部场景完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
