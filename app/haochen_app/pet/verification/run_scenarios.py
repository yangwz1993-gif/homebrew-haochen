#!/usr/bin/env python3
"""桌宠模块 P3 验证驱动：全程 mock 引擎，自动跑验收场景并截图存证。

    cd app
    MOCK_TICK_MS=20 .venv/bin/python haochen_app/pet/verification/run_scenarios.py

场景（对应验收清单）：
  S0 首启问称呼  → 首次唤起即锚定人物正上方（间隙 8px）+ 问候块 → 输入称呼落盘不再问（v0.1.7）
  S1 普通问答    → 输入退场/思考状态/单轮结果卡，姿态 idle→thinking→idle
  S2 读屏确认    → 感知提示（单行不折行）+「读吧/不读」确认条 → 回车=「读吧」→ 短结（v0.1.7）
  S3 错误路径    → 错误块 + alert(angry) 姿态
  S4 Esc 打断    → abort，已产内容保留，状态条标「已停止」
  S5 Esc 收起    → 气泡淡出，状态回 IDLE（失焦不收起由 changeEvent 保证，人工可验）
  S6 查看详情    → ChatWindow 从气泡 rect 展开；Esc 收起 → app 不退出、窗口与旧结果都退场
  S7 展开后 ⌘W   → 同上（⌘W 快捷键槽 + closeEvent 两条路径同样只收出不退出）
  S8 几何/联动   → 气泡在人物正上方不重叠；拖人物→气泡跟随、拖气泡→人物跟随；位置记忆恢复（v0.1.6）

截图：pet/bubble 各自 widget.grab()（不依赖屏幕坐标、不拍用户桌面）。
日志：state-transitions.log（状态机迁移）+ results.log（断言结果）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APP_DIR))
OUT = Path(__file__).resolve().parent

# v0.1.6 位置记忆写 haochen_home()/pet-pos.json：验证隔离到临时目录，不碰真实数据目录
os.environ.setdefault("HAOCHEN_HOME", tempfile.mkdtemp(prefix="haochen-pet-verify-"))
# 留出足够时间让 ACK 卡完成淡入；只影响本视觉证据进程，mock 默认仍为零延迟。
os.environ.setdefault("MOCK_ACCEPT_DELAY_MS", "240")

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton

from haochen_app.pet import PetApp, PetState
from haochen_app.pet.bubble import GreetBlock, HintBlock, SummaryBlock
from haochen_app.pet.pet_window import PetWindow
from haochen_app.pet.profile import should_ask_name

LOG: list[str] = []
VISUAL_EVIDENCE: list[dict] = []


def note(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    LOG.append(line)
    print(line, flush=True)


def shot(widget, name: str) -> None:
    widget.grab().save(str(OUT / name))
    note(f"screenshot -> {name}")


def shot_pair(pa: PetApp, name: str) -> None:
    """Capture pet and bubble as one neutral-canvas artifact plus exact geometry."""
    b, p = pa.bubble, pa.pet
    bounds = b.geometry().united(p.geometry())
    canvas = QPixmap(bounds.size())
    canvas.fill(QColor("#f1f0ec"))
    painter = QPainter(canvas)
    painter.drawPixmap(b.pos() - bounds.topLeft(), b.grab())
    painter.drawPixmap(p.pos() - bounds.topLeft(), p.grab())
    painter.end()
    canvas.save(str(OUT / name))

    tail_x = b.x() + b.width() - 34
    pet_center_x = p.x() + p.width() // 2
    VISUAL_EVIDENCE.append({
        "file": name,
        "state": pa.state.value,
        "bubble": {"x": b.x(), "y": b.y(), "width": b.width(), "height": b.height()},
        "pet": {"x": p.x(), "y": p.y(), "width": p.width(), "height": p.height()},
        "bubble_pet_gap": p.y() - (b.y() + b.height()),
        "tail_center_delta_x": tail_x - pet_center_x,
        "layout": b.layout_metrics(),
    })
    note(f"evidence -> {name} {VISUAL_EVIDENCE[-1]}")


class Runner:
    """按信号串联场景：每幕完成才进下一幕，避免定时不稳。"""

    def __init__(self, pa: PetApp):
        self.pa = pa
        self.steps = [self.s0_first_run, self.s1_normal, self.s2_read_screen, self.s3_error,
                      self.s4_abort, self.s5_dismiss, self.s6_expand_esc,
                      self.s7_expand_cmd_w, self.s8_geometry_drag_persist, self.finish]
        self.results: list[str] = []
        self.app_quit = False
        self._captured_transitions: set[str] = set()
        QApplication.instance().aboutToQuit.connect(self._mark_quit)
        # v0.1.4 hotfix：详情改走 ChatWindow「从气泡展开」模式。
        # 这里在测试内复刻壳层接线（app_shell 应由集成方加同样两行）。
        from haochen_app.chat.theme import app_stylesheet
        from haochen_app.chat.window import ChatWindow
        self.chat = ChatWindow(client=pa.client)
        self.chat.setStyleSheet(app_stylesheet())  # 与壳层一致，截图目检呈真机样式
        pa.detail_opener = self.chat.open_from_bubble
        self.chat.detail_collapsed.connect(pa.restore_bubble)
        pa.state_changed.connect(self._on_state_changed)

    def _on_state_changed(self, state: str) -> None:
        note(f"state -> {state}")
        if (state in {"ACKNOWLEDGING", "ACTING"}
                and state not in self._captured_transitions
                and self.pa.bubble.summoned):
            self._captured_transitions.add(state)
            delay = 190 if state == "ACKNOWLEDGING" else 0
            QTimer.singleShot(
                delay,
                lambda captured=state: shot_pair(
                    self.pa, f"evidence-{captured.lower()}.png"
                ),
            )

    def _mark_quit(self) -> None:
        self.app_quit = True

    def check(self, name: str, ok: bool, extra: str = "") -> None:
        self.results.append(f"{'PASS' if ok else 'FAIL'}  {name}  {extra}")
        note(f"[{'PASS' if ok else 'FAIL'}] {name} {extra}")

    def next(self, delay: int = 400) -> None:
        QTimer.singleShot(delay, self._run_step)

    def submit_user_text(self, text: str) -> None:
        """走和真实用户完全相同的输入/发送路径，避免验收证据保留了隐藏输入框。"""
        pa = self.pa
        if not pa.bubble._input_visible():
            pa._on_continue()
        pa.bubble.input.setPlainText(text)
        pa.bubble._on_send()

    def _run_step(self) -> None:
        if self.steps:
            self.steps.pop(0)()

    # ── S0 首启问称呼 + 首次唤起即锚定（v0.1.7）──
    def s0_first_run(self):
        note("── S0 首次使用：默认锚定 + 问称呼 ──")
        pa = self.pa
        self.check("S0 首次使用应问称呼（无 user-profile.json）", should_ask_name())
        pa._toggle_bubble()  # 启动后第一次唤起
        QTimer.singleShot(700, self._s0_greeting)

    def _s0_greeting(self):
        pa = self.pa
        b, p = pa.bubble, pa.pet
        # 默认锚定：唤起后气泡即在人物正上方（间隙 8px，尾巴对中心），不用拖
        gap = p.y() - (b.y() + b.height())
        self.check("S0 首次唤起气泡在人物正上方", b.y() + b.height() <= p.y(),
                   f"b.bottom={b.y() + b.height()} p.top={p.y()}")
        self.check("S0 间隙恰为 8px", gap == 8, f"gap={gap}")
        tail_x = b.x() + b.width() - 34
        self.check("S0 尾巴尖对准人物中心", abs(tail_x - (p.x() + p.width() // 2)) <= 2,
                   f"tail={tail_x} center={p.x() + p.width() // 2}")
        # v0.2.0：称呼由首启向导询问，气泡唤起不再自动拦截
        self.check("S0 气泡唤起不再自动问称呼（已移向导）", not pa._awaiting_name)
        shot(pa.bubble, "00-first-run-ask-name.png")
        pa._awaiting_name = True  # 主动验证称呼回合链路（档案保存、不进引擎）
        self.submit_user_text("阿晨")
        QTimer.singleShot(300, self._s0_saved)

    def _s0_saved(self):
        pa = self.pa
        profile_path = Path(os.environ["HAOCHEN_HOME"]) / "user-profile.json"
        data = json.loads(profile_path.read_text(encoding="utf-8")) \
            if profile_path.exists() else {}
        self.check("S0 称呼已落盘 user-profile.json", data.get("name") == "阿晨", str(data))
        self.check("S0 称呼回合不进引擎", not pa.ctrl.busy)
        self.check("S0 不再等待称呼", not pa._awaiting_name)
        self.check("S0 再次启动不再问（档案已存在）", not should_ask_name())
        shot(pa.bubble, "00b-first-run-named.png")
        pa._on_escape()  # 收起回 IDLE，供 S1 从初始状态起跑
        QTimer.singleShot(600, self.next)

    # ── S1 普通问答 ──
    def s1_normal(self):
        note("── S1 普通问答（气泡出短结）──")
        pa = self.pa
        self.check("初始状态 IDLE", pa.state is PetState.IDLE)
        shot(pa.pet, "01-pet-idle.png")
        pa._toggle_bubble()  # 模拟双击唤起
        self.check("唤起后 LISTENING", pa.state is PetState.LISTENING)
        QTimer.singleShot(300, lambda: (
            shot(pa.bubble, "02-bubble-summoned.png"),
            shot_pair(pa, "evidence-listening.png"),
        ))
        QTimer.singleShot(500, lambda: self.submit_user_text("你好，haochen，介绍一下你自己"))
        # v0.3.0：请求接单后由真实引擎事件进入 COMPOSING。
        QTimer.singleShot(1500, lambda: (
            shot(pa.pet, "03a-pet-thinking.png"),
            shot(pa.bubble, "03b-bubble-thinking.png"),
            shot_pair(pa, "evidence-composing.png"),
            self.check("生成中姿态 thinking", pa.pet.pose == "thinking", pa.pet.pose),
            self.check("生成中状态 COMPOSING", pa.state is PetState.COMPOSING, pa.state.value)))
        pa.ctrl.summary_done.connect(self._s1_done)

    def _s1_done(self, summary: str):
        pa = self.pa
        pa.ctrl.summary_done.disconnect(self._s1_done)
        QTimer.singleShot(250, lambda: self._s1_result_ready(summary))

    def _s1_result_ready(self, summary: str):
        pa = self.pa
        shot(pa.bubble, "04-summary-l1.png")
        shot_pair(pa, "evidence-presenting.png")
        self.check("S1 短结非空", bool(summary.strip()), summary[:30])
        blocks = pa.bubble.findChildren(SummaryBlock)
        self.check("S1 结果卡已稳定渲染", bool(blocks))
        self.check("S1 结果阶段不常驻输入框", not pa.bubble._input_visible())
        self.check("S1 结果卡提供继续问与查看详情", bool(blocks)
                   and blocks[-1].continue_button.text() == "继续问"
                   and blocks[-1].expand_button.text() == "查看详情")
        forbidden = ("haochen-summary-phase", "【answer】", "【summary】")
        self.check("S1 用户结果不泄露协议词", not any(x in summary for x in forbidden))
        self.check("S1 结果进入 PRESENTING", pa.state is PetState.PRESENTING, pa.state.value)
        QTimer.singleShot(250, lambda: (shot(pa.pet, "04b-pet-back-idle.png"),
                                        self.check("姿态回 idle", pa.pet.pose == "idle")))
        QTimer.singleShot(600, self.next)

    # ── S2 读屏确认链路 ──
    def s2_read_screen(self):
        note("── S2 读屏确认（感知提示 + 读吧/不读）──")
        pa = self.pa
        self._s2_answer = ""
        pa.ctrl.answer_done.connect(self._s2_answer_got)
        self.submit_user_text("帮我读屏看看屏幕上有什么")
        # v0.2.0：消息经队列异步泄流，确认条出现时间稍晚（mock tick × 多轮）
        self._s2_confirm_polls = 0
        QTimer.singleShot(2000, self._s2_wait_confirm)
        pa.ctrl.summary_done.connect(self._s2_done)

    def _s2_wait_confirm(self):
        pa = self.pa
        if pa.bubble.confirm_pending or self._s2_confirm_polls >= 6:
            self._s2_confirm_shown()
            return
        self._s2_confirm_polls += 1
        QTimer.singleShot(1000, self._s2_wait_confirm)

    def _s2_answer_got(self, answer: str):
        self._s2_answer = answer

    def _s2_confirm_shown(self):
        pa = self.pa
        ok = pa.bubble.confirm_pending
        self.check("S2 确认条已弹出", ok)
        self.check("S2 状态 PERCEIVING/ACTING",
                   pa.state in (PetState.PERCEIVING, PetState.ACTING),
                   pa.state.value)
        shot(pa.bubble, "05-read-screen-confirm.png")
        shot_pair(pa, "evidence-perceiving.png")
        # v0.1.7：感知提示单行不折行
        hint = pa.bubble.findChild(HintBlock)
        if hint:
            lb = hint.label
            self.check("S2 感知提示单行不折行",
                       lb.sizeHint().height() <= lb.fontMetrics().lineSpacing() + 6,
                       f"hintH={lb.sizeHint().height()} line={lb.fontMetrics().lineSpacing()}")
        else:
            self.check("S2 感知提示单行不折行", False, "HintBlock 未出现")
        bar = pa.bubble._confirm_bar
        if bar:
            for btn in bar.findChildren(QPushButton):
                if btn.text() == "读吧":
                    # v0.1.6：「读吧」必须醒目绿（白字加粗），不允许浅白看不清
                    self.check("S2 读吧按钮醒目绿", "#3a7d5c" in btn.styleSheet()
                               and "bold" in btn.styleSheet(), btn.styleSheet()[:60])
            # v0.1.7：确认条可见时输入框回车 = 确认「读吧」
            QTimer.singleShot(300, lambda: QTest.keyClick(
                pa.bubble.input, Qt.Key.Key_Return))
            QTimer.singleShot(700, self._s2_enter_resolved)

    def _s2_enter_resolved(self):
        pa = self.pa
        self.check("S2 回车后确认条已解决", not pa.bubble.confirm_pending)
        self.check("S2 回车=允许读屏（confirm 已答复引擎）", pa._confirm_id is None)

    def _s2_done(self, summary: str):
        pa = self.pa
        pa.ctrl.summary_done.disconnect(self._s2_done)
        pa.ctrl.answer_done.disconnect(self._s2_answer_got)
        QTimer.singleShot(250, lambda: self._s2_result_ready(summary))

    def _s2_result_ready(self, summary: str):
        pa = self.pa
        shot(pa.bubble, "06-read-screen-done.png")
        self.check("S2 读屏后短结非空", bool(summary.strip()), summary[:30])
        self.check("S2 读屏结果不恢复输入框", not pa.bubble._input_visible())
        # mock 的 brief 文案固定，读屏语义改在 detail 详答上验
        self.check("S2 详答含读屏语义", "屏" in self._s2_answer, self._s2_answer[:40])
        QTimer.singleShot(300, self.next)

    # ── S3 错误路径 ──
    def s3_error(self):
        note("── S3 错误路径（alert 姿态 + 错误块）──")
        pa = self.pa
        self.submit_user_text("这里触发一个错误")
        pa.ctrl.failed.connect(self._s3_failed)

    def _s3_failed(self, err: str):
        pa = self.pa
        pa.ctrl.failed.disconnect(self._s3_failed)
        self.check("S3 failed 信号到达", bool(err), err[:40])
        QTimer.singleShot(600, lambda: (
            shot(pa.pet, "07a-pet-alert.png"),
            shot(pa.bubble, "07b-error-block.png"),
            shot_pair(pa, "evidence-error.png"),
            self.check("S3 alert(angry) 姿态", pa.pet.pose == "angry", pa.pet.pose)))
        QTimer.singleShot(1200, self.next)

    # ── S4 Esc 打断 ──
    def s4_abort(self):
        note("── S4 Esc 打断（abort，保留已产内容）──")
        pa = self.pa
        self.submit_user_text("再介绍一下你自己，这次我会打断你")
        QTimer.singleShot(900, self._s4_do_abort)
        pa.ctrl.summary_done.connect(self._s4_done)
        pa.ctrl.failed.connect(self._s4_done)

    def _s4_do_abort(self):
        pa = self.pa
        pa._on_escape()  # 生成中 Esc = 打断
        self.check("S4 打断标记", pa._aborted)
        QTimer.singleShot(300, lambda: (
            shot(pa.bubble, "08-abort-stopped.png"),
            shot_pair(pa, "evidence-cancelled.png"),
        ))

    def _s4_done(self, *_):
        pa = self.pa
        try:
            pa.ctrl.summary_done.disconnect(self._s4_done)
            pa.ctrl.failed.disconnect(self._s4_done)
        except TypeError:
            pass
        self.check("S4 打断后流程终结（不卡死）", not pa.ctrl.busy)
        QTimer.singleShot(300, self.next)

    # ── S5 Esc 收起 ──
    def s5_dismiss(self):
        note("── S5 Esc 收起（失焦不收起的反面：显式收起）──")
        pa = self.pa
        pa._on_escape()  # 空闲 Esc = 收起
        QTimer.singleShot(400, lambda: (
            self.check("S5 气泡已收起", not pa.bubble.summoned),
            self.check("S5 状态回 IDLE", pa.state is PetState.IDLE, pa.state.value),
            self.next()))

    # ── S6 展开详细 → Esc 收起（回归：Esc 不得退出整个 app）──
    def s6_expand_esc(self):
        note("── S6 展开详细 → ChatWindow 展开 → Esc 收起不退出 ──")
        pa = self.pa
        pa._toggle_bubble()  # 重新唤起气泡
        QTimer.singleShot(300, lambda: self._expand_then("Esc", self._close_by_esc, self.next))

    def _close_by_esc(self):
        QTest.keyClick(self.chat, Qt.Key.Key_Escape)

    def _close_by_cmd_w(self):
        # offscreen 下窗口无法 activateWindow，QTest 组合键打不中 WindowShortcut；
        # 直接触发 ⌘W 快捷键槽（真机等价链路：QShortcut.activated → _on_close_shortcut）
        self.chat._on_close_shortcut()

    def _close_by_close_event(self):
        self.chat.close()  # closeEvent 路径（系统级关闭/Mission Control）

    def _expand_then(self, tag: str, closer, after):
        """展开详情 → 断言展开态 → closer() 收起 → 断言窗口和旧结果都退场。"""
        pa = self.pa
        if not pa.bubble.summoned:
            pa._toggle_bubble()
        pa._on_expand_detail()
        QTimer.singleShot(500, lambda: self._expanded_check(tag, closer, after))

    def _expanded_check(self, tag: str, closer, after):
        chat = self.chat
        self.check(f"{tag} 详情模式打开（对话窗口可见）",
                   chat.isVisible() and chat._detail_mode)
        shot(chat, f"09-detail-expanded-{tag}.png")
        closer()
        # 收起动画 ~220ms，留足余量再断言
        QTimer.singleShot(600, lambda: self._collapsed_check(tag, after))

    def _collapsed_check(self, tag: str, after):
        pa, chat = self.pa, self.chat
        self.check(f"{tag} 后 app 未退出（无 aboutToQuit）", not self.app_quit)
        self.check(f"{tag} 后对话窗口已隐藏", not chat.isVisible())
        self.check(f"{tag} 后退出详情模式", not chat._detail_mode)
        self.check(f"{tag} 后旧结果未恢复常驻", not pa.bubble.isVisible()
                   and not pa.bubble.summoned)
        self.check(f"{tag} 后桌宠回 IDLE", pa.state is PetState.IDLE, pa.state.value)
        QTimer.singleShot(300, after)

    # ── S7 展开详细 → ⌘W / closeEvent 收起 ──
    def s7_expand_cmd_w(self):
        note("── S7 展开详细 → ⌘W 收起不退出（再验 closeEvent 路径）──")
        self._expand_then(
            "⌘W", self._close_by_cmd_w,
            lambda: self._expand_then("closeEvent", self._close_by_close_event, self.next))

    # ── S8 气泡几何 + 联动拖动 + 位置记忆（v0.1.6）──
    def s8_geometry_drag_persist(self):
        note("── S8 几何/联动拖动/位置记忆 ──")
        pa = self.pa
        screen = QApplication.primaryScreen().availableGeometry()
        # 人物摆到屏幕中部偏下，避开贴边 clamp 干扰几何断言
        pa.pet.move(screen.center().x() - pa.pet.width() // 2,
                    screen.top() + int(screen.height() * 0.55))
        if not pa.bubble.summoned:
            pa._toggle_bubble()
        # 前几幕积累的对话流会把气泡撑到上限 → 触发"落下方"fallback；清空再锚定
        pa.bubble.clear_flow()
        pa.bubble._refresh_height()
        pa._place_bubble()  # 程序化 move 不发 moved 信号，显式重锚定
        QTimer.singleShot(500, self._s8_geometry)

    def _s8_gap(self) -> int:
        """气泡与人物的垂直间隙：上方 = 气泡底（含尾巴）到头顶；下方 = 人物底到气泡顶。"""
        b, p = self.pa.bubble, self.pa.pet
        if b.y() + b.height() <= p.y():
            return p.y() - (b.y() + b.height())
        return b.y() - (p.y() + p.height())

    def _s8_geometry(self):
        pa = self.pa
        b, p = pa.bubble, pa.pet
        gap = self._s8_gap()
        self.check("S8 气泡与人物不重叠（间隙 6~10px）", 6 <= gap <= 10, f"gap={gap}")
        self.check("S8 气泡在人物正上方", b.y() + b.height() <= p.y(),
                   f"b.bottom={b.y() + b.height()} p.top={p.y()}")
        tail_x = b.x() + b.width() - 34  # 尾巴尖全局 x（paintEvent: tail_x=w-44, 尖 +10）
        self.check("S8 尾巴尖对准人物中心", abs(tail_x - (p.x() + p.width() // 2)) <= 2,
                   f"tail={tail_x} center={p.x() + p.width() // 2}")
        # 内容增长（气泡变高）后重新锚定，仍不重叠
        pa.bubble.add_summary("内容增长锚定测试 " * 6)
        QTimer.singleShot(400, self._s8_growth)

    def _s8_growth(self):
        gap = self._s8_gap()
        self.check("S8 内容增长后仍不重叠", gap >= 6, f"gap={gap}")
        # 拖动人物（+120,+20）→ 气泡跟随仍在正上方
        # 注：窗口跟手后下一次 mouseMove 的局部坐标会叠加，一步到位即净位移 (130-10, 30-10)
        p = self.pa.pet
        before = p.pos()
        M = Qt.KeyboardModifier.NoModifier
        QTest.mousePress(p, Qt.MouseButton.LeftButton, M, QPoint(10, 10))
        QTest.mouseMove(p, QPoint(130, 30))
        QTest.mouseRelease(p, Qt.MouseButton.LeftButton, M, QPoint(130, 30))
        self.check("S8 人物已拖动", p.pos() == before + QPoint(120, 20),
                   f"{before}->{p.pos()}")
        QTimer.singleShot(200, self._s8_pet_dragged)

    def _s8_pet_dragged(self):
        pa = self.pa
        b, p = pa.bubble, pa.pet
        gap = self._s8_gap()
        self.check("S8 拖人物后气泡跟随不重叠", 6 <= gap <= 10, f"gap={gap}")
        tail_x = b.x() + b.width() - 34
        self.check("S8 拖人物后尾巴仍对准中心", abs(tail_x - (p.x() + p.width() // 2)) <= 2,
                   f"tail={tail_x} center={p.x() + p.width() // 2}")
        # 拖动结束位置已写入 pet-pos.json
        pos_file = Path(os.environ["HAOCHEN_HOME"]) / "pet-pos.json"
        data = json.loads(pos_file.read_text(encoding="utf-8")) if pos_file.exists() else {}
        self.check("S8 位置写入 pet-pos.json",
                   data.get("x") == p.x() and data.get("y") == p.y(), str(data))
        # 拖气泡（+60,0）→ 人物跟到尾巴正下方（顶部空白区按下，一步到位）
        M = Qt.KeyboardModifier.NoModifier
        b_before = b.pos()
        QTest.mousePress(b, Qt.MouseButton.LeftButton, M, QPoint(200, 4))
        QTest.mouseMove(b, QPoint(260, 4))
        QTest.mouseRelease(b, Qt.MouseButton.LeftButton, M, QPoint(260, 4))
        self.check("S8 气泡已拖动", b.pos() == b_before + QPoint(60, 0),
                   f"{b_before}->{b.pos()}")
        QTimer.singleShot(200, self._s8_bubble_dragged)

    def _s8_bubble_dragged(self):
        pa = self.pa
        b, p = pa.bubble, pa.pet
        tail_x = b.x() + b.width() - 34
        self.check("S8 拖气泡后人物跟到尾巴正下方",
                   abs(tail_x - (p.x() + p.width() // 2)) <= 2
                   and p.y() - (b.y() + b.height()) == 8,
                   f"tail={tail_x} center={p.x() + p.width() // 2} "
                   f"gap={p.y() - b.y() - b.height()}")
        # 拖气泡结束同样持久化人物位置
        data = json.loads((Path(os.environ["HAOCHEN_HOME"]) / "pet-pos.json")
                          .read_text(encoding="utf-8"))
        self.check("S8 拖气泡后位置已更新", data.get("x") == p.x() and data.get("y") == p.y(),
                   str(data))
        # 重建 PetWindow → 恢复记忆位置
        p2 = PetWindow()
        p2.set_floating(False)  # 关 idle 浮动，防 ±2px 干扰断言
        self.check("S8 重建后恢复记忆位置", p2.pos() == p.pos(),
                   f"{p2.pos()} vs {p.pos()}")
        p2.deleteLater()
        shot(pa.bubble, "11-bubble-after-drag.png")
        shot(pa.pet, "12-pet-after-drag.png")
        QTimer.singleShot(300, self.next)

    # ── 收尾 ──
    def finish(self):
        (OUT / "state-transitions.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
        (OUT / "visual-metrics.json").write_text(
            json.dumps(VISUAL_EVIDENCE, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report = "\n".join(self.results) + "\n"
        (OUT / "results.log").write_text(report, encoding="utf-8")
        print("\n===== RESULTS =====\n" + report, flush=True)
        self.pa.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("haochen-pet-verification")
    app.setQuitOnLastWindowClosed(False)
    pa = PetApp(mock=True)
    pa.start()
    runner = Runner(pa)
    QTimer.singleShot(500, runner.next)
    QTimer.singleShot(120000, app.quit)  # 全局兜底
    code = app.exec()
    pa.client.stop()
    failed = any(line.startswith("FAIL") for line in runner.results)
    return 1 if failed else code


if __name__ == "__main__":
    sys.exit(main())
