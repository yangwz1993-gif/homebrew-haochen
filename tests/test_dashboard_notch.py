"""Public notch geometry and status normalization without external UI access."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
notch = importlib.import_module("haochen_app.dashboard.notch")
native = importlib.import_module("haochen_app.dashboard.native_window")


def test_notch_entrance_is_flush_and_seamless_within_the_gap():
    frame, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 76, 1470, 847), 32,
        (0, 924, 646, 32), (825, 924, 645, 32))
    assert attached
    # Flush with the screen top so the pure-black fill is continuous with the
    # hardware notch (it grows downward, not a capsule hanging below).
    assert frame[1] + frame[3] == 956
    assert frame[3] == 32 + notch.NOTCH_DROP  # notch height + label band
    # Stays inside the notch gap: no side menu/status item is covered.
    assert frame[0] == 646 and frame[0] + frame[2] == 825


def test_attached_entrance_is_pure_black_to_match_the_notch():
    # A near-black fill is the seam users report; attached must be pure black
    # so it is the same material as the hardware notch.
    assert notch.entrance_fill(True) == (0.0, 1.0)
    assert notch.entrance_fill(True, highlighted=True)[1] == 1.0
    # The free-floating capsule stays a visible dark chip over any wallpaper.
    white, alpha = notch.entrance_fill(False)
    assert white > 0 and alpha < 1.0


def test_attached_corner_radius_is_tighter_than_floating_capsule():
    assert notch.entrance_corner_radius(True) < notch.entrance_corner_radius(False)


def test_geometry_is_snapped_to_the_pixel_grid():
    # Fractional screen metrics must not leave an antialiased seam.
    frame, attached = notch.entrance_geometry(
        (0, 0, 1471, 957), (0, 76, 1471, 847), 32,
        (0, 924, 646.4, 32), (825.6, 924, 645, 32))
    assert attached
    assert all(v == round(v) for v in frame)


@pytest.mark.parametrize("origin", [(-1920, 0), (0, 956), (1470, -200)])
def test_external_display_capsule_stays_within_its_own_visible_frame(origin):
    x, y = origin
    frame, attached = notch.entrance_geometry((x, y, 1920, 1080), (x, y, 1920, 1055))
    assert not attached
    assert x <= frame[0] < frame[0] + frame[2] <= x + 1920
    assert y <= frame[1] < frame[1] + frame[3] < y + 1055


def test_hidden_menu_keeps_entrance_inside_the_notch_gap():
    frame, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 0, 1470, 956), 32,
        (0, 924, 646, 32), (825, 924, 645, 32))
    assert attached
    # Even with the menu bar auto-hidden, the width never spills past the gap
    # into where side menu/status items sit.
    assert frame[0] >= 646 and frame[0] + frame[2] <= 825


def test_empty_or_invalid_auxiliary_gap_falls_back_to_capsule():
    frame, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 0, 1470, 924), 32,
        (0, 0, 0, 0), (0, 0, 0, 0))
    assert not attached
    assert frame[1] + frame[3] < 924


def test_activity_never_invents_unread_count_or_status():
    assert notch.activity_from(None) == notch.Activity()
    assert notch.activity_from({"kind": "fake", "count": "3"}) == notch.Activity()
    assert notch.activity_from({"kind": [], "label": None}) == notch.Activity()
    activity = notch.activity_from({"kind": "new", "count": 3, "label": "有新结果"})
    assert (activity.kind, activity.count, activity.label) == ("new", 3, "有新结果")
    assert notch.activity_from({"kind": "attention", "count": -1}).count == 0
    assert notch.activity_from({"kind": "error", "count": True}).count == 0
    assert notch.activity_from({"kind": "running", "count": 1200}).count == 999


def test_command_m_does_not_consume_modified_or_unrelated_commands():
    assert notch.command_m("m", 1, 1, 2, 4, 8)
    assert notch.command_m("M", 17, 1, 2, 4, 8)  # Caps Lock ignored
    for char, flags in [("m", 0), ("m", 3), ("m", 5), ("m", 9), ("n", 1), (None, 1)]:
        assert not notch.command_m(char, flags, 1, 2, 4, 8)


def test_main_dashboard_never_uses_floating_all_spaces_window():
    source = (Path(__file__).resolve().parents[1]
              / "app/haochen_app/dashboard/native_window.py").read_text()
    assert "setLevel_(AK.NSNormalWindowLevel)" in source
    assert "NSFloatingWindowLevel" not in source
    assert "NSWindowCollectionBehaviorCanJoinAllSpaces" not in source
    assert "QCursor" not in source


def test_anchor_screen_is_stable_until_removed(monkeypatch):
    class Screen:
        def __init__(self, identifier, inset):
            self.identifier, self.inset = identifier, inset

        def deviceDescription(self):
            return {"NSScreenNumber": self.identifier}

        def respondsToSelector_(self, _selector):
            return True

        def safeAreaInsets(self):
            return SimpleNamespace(top=self.inset)

    builtin, external = Screen(1, 32), Screen(2, 0)
    screens = [external, builtin]
    api = SimpleNamespace(screens=lambda: screens, mainScreen=lambda: screens[0])
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSScreen=api))
    owner = SimpleNamespace(_screen_id=None)
    assert native.NativeDashboard._ns_screen(owner) is builtin
    assert owner._screen_id == 1
    assert native.NativeDashboard._ns_screen(owner) is builtin  # does not follow main/cursor
    screens.remove(builtin)
    assert native.NativeDashboard._ns_screen(owner) is external
    assert owner._screen_id == 2
    screens.append(builtin)
    assert native.NativeDashboard._ns_screen(owner) is external  # no teleport on reconnect


# ── 0.6.2-beta.3 刘海动效对齐（WP1/WP2）准出测试 ──────────────────────────

def test_hover_state_machine_transitions():
    """hover 状态机：idle→hover_enter→expanded→hover_exit→idle，且全程可中断续接。"""
    m = notch.hover_next
    assert m("idle", "enter") == "hover_enter"
    assert m("hover_enter", "anim_done") == "expanded"
    assert m("expanded", "exit") == "hover_exit"
    assert m("hover_exit", "anim_done") == "idle"
    # 中断续接：展开中移开 → 直接转收起；收起中回移 → 直接转展开
    assert m("hover_enter", "exit") == "hover_exit"
    assert m("hover_exit", "enter") == "hover_enter"
    # 无关事件不迁移；非法状态回到 idle
    assert m("idle", "exit") == "idle"
    assert m("expanded", "enter") == "expanded"
    assert m("bogus", "enter") == "idle"


def test_animation_reversal_is_continuous():
    """动画 40% 处反向：反向序列的首帧 == 正向序列的当前帧（无跳变）。"""
    closed = (646.0, 890.0, 179.0, 66.0)
    opened = (215.0, 152.0, 1040.0, 740.0)
    mid = notch.interpolate_frame(closed, opened, notch.spring_value(0.4))
    first_reverse = notch.interpolate_frame(mid, closed, notch.spring_value(0.0))
    assert all(abs(a - b) < 1e-9 for a, b in zip(mid, first_reverse))
    # 反向起点之后弹簧继续平滑推进
    later = notch.interpolate_frame(mid, closed, notch.spring_value(0.1))
    assert all(abs(a - b) > 0 for a, b in zip(mid, later))


def test_reduced_motion_zeroes_all_animation_durations():
    """系统减弱动态效果：hover / 状态过渡 / 收展形变的时长全部为 0。"""
    assert notch.hover_duration(True) == 0
    assert notch.transition_duration(True) == 0
    assert notch.morph_duration(True, opening=True) == 0
    assert notch.morph_duration(True, opening=False) == 0
    assert notch.hover_duration(False) > 0
    assert notch.transition_duration(False) > 0
    assert notch.morph_duration(False, opening=True) > 0


def test_hover_expanded_geometry_never_covers_menu_bar():
    """展开态几何边界：贴合刘海时顶部带严格留在缺口内（不遮状态项），只向下生长。"""
    base, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 76, 1470, 847), 32,
        (0, 924, 646, 32), (825, 924, 645, 32))
    assert attached
    hover = notch.hover_frame(base, True, min_x=646, max_x=825)
    assert hover[0] >= 646 and hover[0] + hover[2] <= 825       # 水平不越缺口
    assert hover[1] + hover[3] <= 956                            # 不越过屏幕顶
    assert hover[3] > base[3]                                    # 向下生长
    assert hover[2] <= base[2] + 48                              # 宽度上限 = 缺口宽 + 余量
    # 悬浮胶囊：扩展后仍在屏幕可见区内
    base2, attached2 = notch.entrance_geometry((0, 0, 1920, 1080), (0, 0, 1920, 1055))
    assert not attached2
    hover2 = notch.hover_frame(base2, False, min_x=0, max_x=1920)
    assert hover2[0] >= 0 and hover2[0] + hover2[2] <= 1920
    assert hover2[3] > base2[3]


def test_spring_curve_converges_without_frame_leaving_screen():
    """弹簧曲线：收敛到 1；全程插值 frame 不出屏幕（欠阻尼的轻过冲在安全边距内）。"""
    assert notch.spring_value(0.0) == 0.0
    assert abs(notch.spring_value(1.0) - 1.0) < 1e-3
    screen = (0, 0, 1470, 956)
    closed, attached = notch.entrance_geometry(
        (0, 0, 1470, 956), (0, 76, 1470, 847), 32,
        (0, 924, 646, 32), (825, 924, 645, 32))
    assert attached
    target = (215.0, 152.0, 1040.0, 740.0)
    for i in range(101):
        k = notch.spring_value(i / 100)
        f = notch.interpolate_frame(closed, target, k)
        assert screen[0] - 1 <= f[0] and f[0] + f[2] <= screen[0] + screen[2] + 1
        assert screen[1] - 1 <= f[1] and f[1] + f[3] <= screen[1] + screen[3] + 1
    # 反向（展开 → 收起）同样不出屏幕
    for i in range(101):
        f = notch.interpolate_frame(target, closed, notch.spring_value(i / 100))
        assert screen[0] - 1 <= f[0] and f[0] + f[2] <= screen[0] + screen[2] + 1
        assert screen[1] - 1 <= f[1] and f[1] + f[3] <= screen[1] + screen[3] + 1


def test_state_transition_has_visible_intermediate_blend():
    """状态过渡存在中间态：交叉淡化时长 >0，颜色混合的中点严格介于两端。"""
    assert notch.transition_duration(False) > 0
    attention = notch.ACTIVITY_PALETTE["attention"]
    idle = notch.ACTIVITY_PALETTE["idle"]
    assert notch.blend_color(attention, idle, 0.0) == attention
    assert notch.blend_color(attention, idle, 1.0) == idle
    mid = notch.blend_color(attention, idle, 0.5)
    assert all(abs(m - (a + b) / 2) < 1e-9 for a, b, m in zip(attention, idle, mid))
    assert mid != attention and mid != idle


def test_pulse_redraws_only_the_dot_region():
    """脉冲只重绘圆点矩形：与 drawRect 圆点几何同源，面积远低于整个胶囊。"""
    width, height, inset = 179.0, 66.0, 32.0
    rect = notch.pulse_dirty_rect(width, height, True, inset, False)
    center_y = notch.content_center_y(height, True, inset, False)
    dot = (15, center_y - 3, 6, 6)  # drawRect_ 的圆点
    assert rect[0] <= dot[0] and rect[1] <= dot[1]
    assert rect[0] + rect[2] >= dot[0] + dot[2] and rect[1] + rect[3] >= dot[1] + dot[3]
    assert rect[2] * rect[3] < width * height * 0.05
    # hover 展开（变高）时圆点随顶部锚定上移，脏矩形也必须跟上
    expanded = notch.pulse_dirty_rect(width, height + notch.NOTCH_DROP, True, inset, False,
                                      idle_band=height - inset)
    assert expanded[1] > rect[1]


def test_pulse_calls_partial_redraw_with_dot_rect():
    """_pulse 实际调用 setNeedsDisplayInRect_（而不是整个按钮的 setNeedsDisplay_）。"""
    recorded = []
    button = SimpleNamespace(
        pulse=0.0, attached=True, notch_inset=32.0, idle_band=34.0,
        bounds=lambda: SimpleNamespace(size=SimpleNamespace(width=179.0, height=66.0)),
        isFlipped=lambda: False,
        setNeedsDisplayInRect_=lambda r: recorded.append(("rect", r)),
        setNeedsDisplay_=lambda v: recorded.append(("full", v)),
    )
    entrance = object.__new__(notch.NativeEntrance)
    entrance.button = button
    notch.NativeEntrance._pulse(entrance, 0.8)
    assert button.pulse == 0.8
    assert len(recorded) == 1 and recorded[0][0] == "rect", recorded
    rect = recorded[0][1]
    expected = notch.pulse_dirty_rect(179.0, 66.0, True, 32.0, False, 34.0)
    assert abs(rect.origin.x - expected[0]) < 1e-9
    assert abs(rect.origin.y - expected[1]) < 1e-9
    assert abs(rect.size.width - expected[2]) < 1e-9
    assert abs(rect.size.height - expected[3]) < 1e-9


def test_pulse_animation_still_exists_after_partial_redraw_change():
    """存在性断言：脉冲动画没有被借「重绘优化」之名删掉。"""
    import inspect
    source = inspect.getsource(notch.NativeEntrance)
    assert "setLoopCount(-1)" in source and "_pulse" in source


# ── WP1/WP2 逻辑层覆盖：subclass/fake 接缝（AppKit 效果录制，逻辑真实走）──────

class _FakeEntrance(notch.NativeEntrance):
    """绕过 AppKit 初始化的 NativeEntrance：面板/按钮用录制型 fake，
    hover/过渡/收放的决策逻辑走真实代码路径。"""

    def __init__(self, *, attached=True, reduced=False, visible=True):
        self._owner = None
        self._attached = attached
        self._base_frame = (646.0, 890.0, 179.0, 66.0) if attached else (857.0, 967.0, 206.0, 38.0)
        self._frame = self._base_frame
        self._hover_state = "idle"
        self._hover_anim = None
        self._hover_generation = 0
        self._transition = None
        self._transition_generation = 0
        self.reduced_motion = reduced
        self.panel = SimpleNamespace(
            isVisible=lambda: visible,
            frame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=self._frame[0], y=self._frame[1]),
                size=SimpleNamespace(width=self._frame[2], height=self._frame[3])),
            setFrame_display_=lambda r, _d: setattr(
                self, "_frame", (r.origin.x, r.origin.y, r.size.width, r.size.height)),
            orderOut_=lambda _s: None,
            close=lambda: None,
        )
        self.button = SimpleNamespace(
            activity=notch.Activity(), pulse=1.0, attached=attached,
            notch_inset=32.0 if attached else 0.0, idle_band=34.0 if attached else 38.0,
            hint_alpha=0.0, blend=1.0, previous_activity=None,
            bounds=lambda: SimpleNamespace(size=SimpleNamespace(
                width=self._frame[2], height=self._frame[3])),
            isFlipped=lambda: False,
            setFrame_=lambda r: None,
            setNeedsDisplay_=lambda v: None,
            setNeedsDisplayInRect_=lambda r: None,
            setAccessibilityLabel_=lambda s: None,
            setToolTip_=lambda s: None,
            setTarget_=lambda s: None,
            disconnect_owner=lambda: None,
        )
        self.animation = SimpleNamespace(
            state=lambda: None, start=lambda: None, stop=lambda: None)


def test_morph_progress_tracks_width_ratio():
    owner = SimpleNamespace(
        _closed_frame=lambda: (646.0, 890.0, 179.0, 66.0),
        _open_frame=lambda: (215.0, 152.0, 1040.0, 740.0))
    assert native.NativeDashboard._morph_progress(owner, (646, 0, 179, 66)) == 0.0
    assert native.NativeDashboard._morph_progress(owner, (215, 0, 1040, 740)) == 1.0
    mid = native.NativeDashboard._morph_progress(owner, (0, 0, (179 + 1040) / 2, 0))
    assert 0.49 < mid < 0.51
    owner._open_frame = owner._closed_frame  # 防御：等宽时进度恒 1
    assert native.NativeDashboard._morph_progress(owner, (646, 0, 179, 66)) == 1.0


def test_morph_corner_radius_between_pill_and_panel():
    assert notch.morph_corner_radius(0, True) == notch.entrance_corner_radius(True)
    assert notch.morph_corner_radius(1, True) == 28.0
    assert notch.morph_corner_radius(0, False) == notch.entrance_corner_radius(False)
    assert notch.entrance_corner_radius(True) < notch.morph_corner_radius(0.5, True) < 28.0


def test_hover_full_cycle_and_reversal(qtbot):
    e = _FakeEntrance(attached=True, reduced=True)  # 0 时长：同步完成
    e.hover(True)
    qtbot.waitUntil(lambda: e._hover_state == "expanded", timeout=1000)
    target = notch.hover_frame(e._base_frame, True, min_x=646, max_x=825)
    assert e._frame == target and e.button.hint_alpha == 1.0
    e.hover(False)
    qtbot.waitUntil(lambda: e._hover_state == "idle", timeout=1000)
    assert e._frame == e._base_frame and e.button.hint_alpha == 0.0


def test_hover_reversal_midflight_keeps_state_machine(qtbot):
    e = _FakeEntrance(attached=True, reduced=False)  # 260ms 动画，不等待完成
    e.hover(True)
    assert e._hover_state == "hover_enter"
    generation = e._hover_generation
    e.hover(False)  # 中途反向
    assert e._hover_state == "hover_exit"
    assert e._hover_generation == generation + 1  # 旧动画被守卫失效
    e._hover_generation += 1  # 测试中收尾，防悬挂动画
    if e._hover_anim is not None:
        e._hover_anim.stop()


def test_hover_ignored_when_panel_hidden_or_no_base(qtbot):
    e = _FakeEntrance(visible=False)
    e.hover(True)
    assert e._hover_state == "idle"  # 面板不可见：不响应
    e2 = _FakeEntrance()
    e2._base_frame = None
    e2.hover(True)
    assert e2._hover_state == "idle"


def test_hover_target_floating_clamps_to_screen(qtbot):
    e = _FakeEntrance(attached=False)
    e._owner = SimpleNamespace(_ns_screen=lambda: SimpleNamespace(
        visibleFrame=lambda: SimpleNamespace(origin=SimpleNamespace(x=0.0),
                                             size=SimpleNamespace(width=1920.0))))
    target = e._hover_target()
    assert target[0] >= 8 and target[0] + target[2] <= 1920 - 8
    assert target[3] > 38  # 向下生长


def test_state_transition_normal_starts_crossfade(qtbot):
    e = _FakeEntrance(reduced=False)
    e.set_activity({"kind": "attention", "count": 2, "label": "等你确认"})
    assert e.button.activity.kind == "attention"
    assert e.button.previous_activity is not None and e.button.previous_activity.kind == "idle"
    assert e.button.blend == 0.0 and e._transition is not None
    e._transition.stop()
    e._transition = None


def test_state_transition_reduced_cuts_immediately(qtbot):
    e = _FakeEntrance(reduced=True)
    e.set_activity({"kind": "running", "count": 1, "label": "正在处理"})
    assert e.button.previous_activity is None and e.button.blend == 1.0
    assert e.button.activity.kind == "running"


def test_same_activity_does_not_retrigger_transition(qtbot):
    e = _FakeEntrance(reduced=False)
    e.set_activity({"kind": "idle"})
    assert e._transition is None  # 未变化不起过渡


def test_hide_resets_hover_to_idle_base_frame(qtbot):
    e = _FakeEntrance(attached=True)
    e._hover_state = "expanded"
    e.button.hint_alpha = 1.0
    e._frame = notch.hover_frame(e._base_frame, True, min_x=646, max_x=825)
    e.hide()
    assert e._hover_state == "idle" and e.button.hint_alpha == 0.0
    assert e._frame == e._base_frame


def test_place_retargets_expanded_hover(qtbot):
    e = _FakeEntrance(attached=True)
    e._hover_state = "expanded"
    new_base = (656.0, 890.0, 179.0, 66.0)
    e.place(new_base, True, reduced=True, notch_inset=32.0)
    assert e._base_frame == new_base
    assert e._frame == notch.hover_frame(new_base, True, min_x=656, max_x=656 + 179)
    assert e.button.idle_band == 34.0


def test_close_stops_all_animations(qtbot):
    e = _FakeEntrance()
    stopped = []
    e._hover_anim = SimpleNamespace(stop=lambda: stopped.append("hover"))
    e._transition = SimpleNamespace(stop=lambda: stopped.append("transition"))
    e.animation = SimpleNamespace(stop=lambda: stopped.append("pulse"))
    e.button.disconnect_owner = lambda: stopped.append("owner")
    e.panel.close = lambda: stopped.append("panel")
    e.close()
    assert set(stopped) == {"hover", "transition", "pulse", "owner", "panel"}


def test_hover_hint_text_attention_loop():
    """WP3：有待确认/异常/新结果时，hover 提示行指名数量与动作；空闲时通用提示。"""
    attention = notch.Activity(kind="attention", count=3)
    assert notch.hover_hint_text(attention) == "3 项等你确认 · 点击处理"
    error = notch.Activity(kind="error", count=2)
    assert notch.hover_hint_text(error) == "2 个连接需处理 · 点击检查"
    fresh = notch.Activity(kind="new", count=1)
    assert notch.hover_hint_text(fresh) == "1 条新结果 · 点击查看"
    idle = notch.Activity(kind="idle")
    assert notch.hover_hint_text(idle) == "点击打开桌面总览"
    # 计数为 0 的 attention 不得虚报数量
    assert notch.hover_hint_text(notch.Activity(kind="attention", count=0)) == "点击打开桌面总览"
