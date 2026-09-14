"""A bounded, public-API notch entrance; it never covers the macOS menu bar.

Geometry is kept independent from AppKit so multiple-display and auto-hidden
menu-bar cases can be tested without reading another application's screen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Activity:
    kind: str = "idle"
    count: int = 0
    label: str = "haochen"
    accessible_label: str = "打开 haochen 桌面总览"


def activity_from(data) -> Activity:
    if not isinstance(data, dict):
        return Activity()
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in {"attention", "error", "new", "running", "idle"}:
        kind = "idle"
    count = data.get("count", 0)
    count = min(999, max(0, count)) if isinstance(count, int) and not isinstance(count, bool) else 0
    label = data.get("label")
    label = " ".join(label.split())[:40] if isinstance(label, str) else ""
    if not label:
        label = {"attention": "等你确认", "error": "连接需处理", "new": "有新结果",
                 "running": "正在处理", "idle": "haochen"}[kind]
    accessible = data.get("accessibleLabel")
    accessible = accessible.strip()[:240] if isinstance(accessible, str) else ""
    default_accessible = ("打开 haochen 桌面总览" if kind == "idle" and label == "haochen"
                          else f"{label}，打开 haochen 桌面总览")
    return Activity(kind, count, label, accessible or default_accessible)


# Visible band below the notch's lower edge where the status label/dot/badge is
# drawn. The attached surface is this tall PLUS the notch height (safe area top).
NOTCH_DROP = 34.0


def _pixel_round(frame):
    """Snap a frame to the whole-point grid.

    Fractional origins/sizes let AppKit antialias the pill edge, which shows up
    as a hairline seam where the attached entrance meets the hardware notch.
    Whole points are pixel-aligned on both 1x and 2x (Retina) displays.
    """
    return tuple(float(round(v)) for v in frame)


def entrance_geometry(frame, visible, safe_top=0, left=None, right=None, dock="notch"):
    """Return (AppKit frame, physically attached).

    On a notched screen the entrance starts at the physical notch's lower edge,
    and is no wider than the public auxiliary-area gap. Its entire hit region is
    below the reserved top area. On other screens it is a normal top capsule.
    All coordinates stay in AppKit's global coordinate space, including screens
    placed to the left or above the primary display. Every returned frame is
    snapped to the pixel grid so the join with the notch has no antialiased seam.
    """
    x, y, width, height = frame
    vx, vy, vw, vh = visible
    if dock == "side":
        return _pixel_round((vx + vw - 194, vy + vh * 2 / 3 - 40, 186, 40)), False
    top = y + height
    safe_top = max(0, min(float(safe_top), 100))
    if safe_top > 0 and left and right:
        gap_left = max(x, left[0] + left[2])
        gap_right = min(x + width, right[0])
        gap = gap_right - gap_left
        if 100 <= gap <= min(400, width / 2):
            # Seamless notch: the surface is flush with the screen's top edge and
            # the same width as the physical notch gap, so its pure-black fill is
            # continuous with the hardware notch (it looks like the notch grew
            # downward, per boring.notch / DynamicNotchKit) rather than a separate
            # capsule hanging below it. It stays inside the gap, so no menu-bar or
            # status item on either side is covered. The label lives in the
            # NOTCH_DROP band below the notch's lower edge.
            return _pixel_round((gap_left, top - safe_top - NOTCH_DROP, gap, safe_top + NOTCH_DROP)), True
    w = min(206, vw - 16)
    return _pixel_round((vx + (vw - w) / 2, min(top - safe_top, vy + vh) - 44, w, 38)), False


# --- Spring + morph + hover pure helpers (testable without AppKit) -----------

_SPRING_ZETA = 0.86   # 欠阻尼：保留一点弹性感
_SPRING_OMEGA = 12.0  # 收敛速度：动画结束时基本归位


def spring_value(t: float, *, zeta: float = _SPRING_ZETA, omega: float = _SPRING_OMEGA) -> float:
    """阻尼弹簧插值（标准化二阶阶跃响应）：t∈[0,1] → 位置系数，收敛到 1。

    欠阻尼允许 ≤0.5% 的轻微过冲（弹性手感）；过冲幅度受 zeta 约束，调用方按
    目标 frame 的屏幕余量保证不出界（现有打开 frame 四周留有 ≥44pt 边距）。
    """
    import math

    t = min(1.0, max(0.0, float(t)))
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    wd = omega * math.sqrt(1.0 - zeta * zeta)
    envelope = math.exp(-zeta * omega * t)
    return 1.0 - envelope * (math.cos(wd * t) + (zeta * omega / wd) * math.sin(wd * t))


def interpolate_frame(start: tuple, target: tuple, k: float) -> tuple:
    """frame 线性插值；k 来自 spring_value。反向续接的连续性由调用方保证
    （start 永远取当前 frame），本函数保持纯计算。"""
    return tuple(a + (b - a) * k for a, b in zip(start, target, strict=True))


def morph_corner_radius(k: float, attached: bool, target: float = 28.0) -> float:
    """收展形变的圆角插值：闭合态 = 入口圆角（贴合刘海 13 / 悬浮胶囊 17），展开 = 面板圆角。"""
    k = min(1.0, max(0.0, float(k)))
    return entrance_corner_radius(attached) + (target - entrance_corner_radius(attached)) * k


_HOVER_DURATION = 0.26       # hover 展开/收起时长（秒）
_TRANSITION_DURATION = 0.18  # 状态交叉淡化时长（秒）
_MORPH_OPEN_DURATION = 0.36  # 总览展开时长（弹簧）
_MORPH_CLOSE_DURATION = 0.30


def hover_duration(reduced: bool) -> float:
    return 0.0 if reduced else _HOVER_DURATION


def transition_duration(reduced: bool) -> float:
    return 0.0 if reduced else _TRANSITION_DURATION


def morph_duration(reduced: bool, *, opening: bool) -> float:
    if reduced:
        return 0.0
    return _MORPH_OPEN_DURATION if opening else _MORPH_CLOSE_DURATION


_HOVER_STATES = ("idle", "hover_enter", "expanded", "hover_exit")
_HOVER_TRANSITIONS = {
    ("idle", "enter"): "hover_enter",
    ("hover_enter", "anim_done"): "expanded",
    ("hover_enter", "exit"): "hover_exit",      # 中途反向：从当前帧连续收起
    ("expanded", "exit"): "hover_exit",
    ("hover_exit", "anim_done"): "idle",
    ("hover_exit", "enter"): "hover_enter",    # 中途反向：从当前帧连续展开
}


def hover_next(state: str, event: str) -> str:
    """hover 状态机：idle ⇄ hover_enter → expanded → hover_exit，全部可中断续接。"""
    if state not in _HOVER_STATES:
        return "idle"
    return _HOVER_TRANSITIONS.get((state, event), state)


def hover_frame(base: tuple, attached: bool, *, min_x: float, max_x: float) -> tuple:
    """hover 展开后的 frame（纯几何）。

    硬性边界：
    - 贴合刘海（attached）时顶部带严格留在缺口内——宽度不变、只向下生长，
      绝不遮住缺口两侧的菜单栏/状态项（展开宽度的物理上限 = 缺口宽 + 余量，
      这里取最保守的「宽度 = 缺口宽」）。
    - 悬浮胶囊/侧边停靠：宽度 ×1.3、向下生长，钳在 [min_x, max_x] 内。
    """
    x, y, w, h = base
    extra_h = NOTCH_DROP if attached else h * 0.9
    if attached:
        new_w, new_x = w, x
    else:
        new_w = w * 1.3
        new_x = x + (w - new_w) / 2
        new_x = max(min_x, min(new_x, max_x - new_w))
    return _pixel_round((new_x, y - extra_h, new_w, h + extra_h))


def blend_color(c1: tuple, c2: tuple, p: float) -> tuple:
    """状态过渡的颜色插值：p=0 → c1，p=1 → c2，中间态为线性混合。"""
    p = min(1.0, max(0.0, float(p)))
    return tuple(a + (b - a) * p for a, b in zip(c1, c2, strict=True))


def content_center_y(height: float, attached: bool, notch_inset: float,
                     flipped: bool, idle_band: float | None = None) -> float:
    """内容（状态点/标签）的垂直中心，顶部锚定：

    收起态与原公式等价（band/2）；hover 展开（高度生长）时内容保持在顶部内容带，
    不随容器增高而下移——drawRect_ 与 pulse_dirty_rect 共用这一几何源。
    """
    inset = notch_inset if attached else 0.0
    band = idle_band if idle_band else (height - inset)
    return (inset + band / 2) if flipped else (height - inset - band / 2)


def pulse_dirty_rect(width: float, height: float, attached: bool,
                     notch_inset: float, flipped: bool,
                     idle_band: float | None = None) -> tuple:
    """脉冲动画只需重绘的圆点矩形（与 drawRect_ 的圆点几何同源，外加 4pt 余量）。

    与 drawRect_ 里 `NSMakeRect(15, center_y - 3, 6, 6)` 严格对应；
    面积远低于整个胶囊（避免 60fps 全量重绘文字）。
    """
    center_y = content_center_y(height, attached, notch_inset, flipped, idle_band)
    x = max(0.0, 15.0 - 4.0)
    y = max(0.0, center_y - 3.0 - 4.0)
    right = min(width, 15.0 + 6.0 + 4.0)
    top = min(height, center_y + 3.0 + 4.0)
    return (x, y, max(0.0, right - x), max(0.0, top - y))


# 五种状态语义色板（MioIsland 对齐）：模块级常量，drawRect 与过渡混合共用。
ACTIVITY_PALETTE = {"attention": (1, 0.76, 0.39), "error": (1, 0.48, 0.42),
                    "new": (0.70, 0.89, 0.73), "running": (0.65, 0.77, 0.98),
                    "idle": (0.65, 0.72, 0.67)}


# --- Pure, testable appearance of the entrance surface -----------------------

def entrance_fill(attached: bool, highlighted: bool = False) -> tuple[float, float]:
    """(calibrated white, alpha) for the entrance background.

    Attached to a hardware notch it must be *pure black* so it is the same
    material as the notch and reads as one continuous shape — a near-black fill
    is the seam users report. The free-floating capsule (no notch / side dock)
    stays a visible dark chip over arbitrary wallpaper.
    """
    if attached:
        return (0.14 if highlighted else 0.0), 1.0
    return (0.20 if highlighted else 0.10), 0.94


def entrance_corner_radius(attached: bool) -> float:
    """Bottom-corner radius. Attached mirrors the hardware notch's tighter
    curve; the floating capsule is rounder."""
    return 13.0 if attached else 17.0


def command_m(characters, flags, command, shift, option, control) -> bool:
    """Ignore Caps Lock, but do not steal another application's/modified key."""
    return (isinstance(characters, str) and characters.lower() == "m"
            and bool(flags & command) and not bool(flags & (shift | option | control)))


def _entrance_classes():
    import AppKit as AK
    import objc

    class HaochenNotchPanel(AK.NSPanel):
        def canBecomeKeyWindow(self):
            return False

        def canBecomeMainWindow(self):
            return False

        def constrainFrameRect_toScreen_(self, frame, _screen):
            # The public safe-area edge may be one point above visibleFrame.
            # AppKit's default menu-bar clamp would leave a white seam between
            # the physical notch and our lower extension. Geometry was already
            # bounded below safeAreaInsets; do not move it a second time.
            return frame

    class HaochenNotchButton(AK.NSButton):
        def updateTrackingAreas_(self):  # noqa: N802 - AppKit 虚方法
            objc.super(HaochenNotchButton, self).updateTrackingAreas()
            old = getattr(self, "_hover_tracking", None)
            if old is not None:
                self.removeTrackingArea_(old)
            area = AK.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                AK.NSTrackingMouseEnteredAndExited | AK.NSTrackingActiveAlways
                | AK.NSTrackingInVisibleRect,
                self, None)
            self.addTrackingArea_(area)
            self._hover_tracking = area

        def mouseEntered_(self, _event):  # noqa: N802 - AppKit 钩子
            entrance = getattr(self, "entrance", None)
            if entrance is not None:
                entrance.hover(True)

        def mouseExited_(self, _event):  # noqa: N802 - AppKit 钩子
            entrance = getattr(self, "entrance", None)
            if entrance is not None:
                entrance.hover(False)

        def drawRect_(self, _dirty):
            bounds = self.bounds()
            width, height = bounds.size.width, bounds.size.height
            radius = entrance_corner_radius(self.attached)
            path = AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius)
            if self.attached:
                # Square top edge is flush with the screen top and continuous with
                # the hardware notch; only the lower corners are rounded.
                top_y = 0 if self.isFlipped() else height / 2
                path.appendBezierPathWithRect_(AK.NSMakeRect(0, top_y, width, height / 2))
            white, alpha = entrance_fill(self.attached, self.isHighlighted())
            AK.NSColor.colorWithCalibratedWhite_alpha_(white, alpha).setFill()
            path.fill()
            inset = self.notch_inset if self.attached else 0.0
            idle_band = getattr(self, "idle_band", 0.0) or (height - inset)
            # 内容顶部锚定：收起时与原「band 居中」公式完全等价，hover 展开（变高）时
            # 内容保持在顶部内容带，不随容器增高而漂移。
            center_y = content_center_y(height, self.attached, self.notch_inset,
                                        self.isFlipped(), idle_band)
            # 状态过渡：kind 切换时新旧状态交叉淡化，不跳变。
            prev = getattr(self, "previous_activity", None)
            blend = getattr(self, "blend", 1.0)
            if prev is not None and blend < 1.0:
                self._draw_activity(prev, 1.0 - blend, bounds, center_y)
                self._draw_activity(self.activity, blend, bounds, center_y)
            else:
                self._draw_activity(self.activity, 1.0, bounds, center_y)
            hint_alpha = getattr(self, "hint_alpha", 0.0)
            if hint_alpha > 0.01:
                extra = height - inset - idle_band
                if extra > 12:
                    if self.isFlipped():
                        hint_center_y = inset + idle_band + extra / 2
                    else:
                        hint_center_y = height - inset - idle_band - extra / 2
                    paragraph = AK.NSMutableParagraphStyle.alloc().init()
                    paragraph.setAlignment_(AK.NSTextAlignmentCenter)
                    attrs = {
                        AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_weight_(
                            11, AK.NSFontWeightRegular),
                        AK.NSForegroundColorAttributeName:
                            AK.NSColor.colorWithCalibratedWhite_alpha_(0.96, 0.55 * hint_alpha),
                        AK.NSParagraphStyleAttributeName: paragraph}
                    AK.NSString.stringWithString_("点击打开桌面总览").drawInRect_withAttributes_(
                        AK.NSMakeRect(8, hint_center_y - 8, width - 16, 16), attrs)

        @objc.python_method
        def _draw_activity(self, activity, alpha_scale, bounds, center_y):
            width = bounds.size.width
            red, green, blue = ACTIVITY_PALETTE.get(activity.kind, ACTIVITY_PALETTE["idle"])
            AK.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                red, green, blue, self.pulse * alpha_scale).setFill()
            AK.NSBezierPath.bezierPathWithOvalInRect_(AK.NSMakeRect(15, center_y - 3, 6, 6)).fill()
            paragraph = AK.NSMutableParagraphStyle.alloc().init()
            paragraph.setLineBreakMode_(AK.NSLineBreakByTruncatingTail)
            attrs = {AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_weight_(13, AK.NSFontWeightSemibold),
                     AK.NSForegroundColorAttributeName:
                         AK.NSColor.colorWithCalibratedWhite_alpha_(0.96, alpha_scale),
                     AK.NSParagraphStyleAttributeName: paragraph}
            badge = "99+" if activity.count > 99 else str(activity.count) if activity.count else ""
            reserve = 30 if badge else 12
            AK.NSString.stringWithString_(activity.label).drawInRect_withAttributes_(
                AK.NSMakeRect(30, center_y - 9, width - 30 - reserve, 18), attrs)
            if badge:
                attrs[AK.NSForegroundColorAttributeName] = (
                    AK.NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, alpha_scale))
                attrs[AK.NSFontAttributeName] = AK.NSFont.monospacedDigitSystemFontOfSize_weight_(
                    12, AK.NSFontWeightBold)
                AK.NSString.stringWithString_(badge).drawInRect_withAttributes_(
                    AK.NSMakeRect(width - 30, center_y - 8, 26, 18), attrs)

        def invoke_(self, _sender):
            if self.owner is not None:
                self.owner.summon_requested.emit()

        @objc.python_method
        def disconnect_owner(self):
            self.owner = None
            self.entrance = None

    return HaochenNotchPanel, HaochenNotchButton


_CLASSES = None


class NativeEntrance:
    """One small non-activating panel, never a full-screen transparent window."""

    def __init__(self, owner):
        import AppKit as AK
        from PyQt6.QtCore import QEasingCurve, QVariantAnimation

        global _CLASSES
        if _CLASSES is None:
            _CLASSES = _entrance_classes()
        Panel, Button = _CLASSES
        self.panel = Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            AK.NSMakeRect(0, 0, 180, 36),
            AK.NSWindowStyleMaskBorderless | AK.NSWindowStyleMaskNonactivatingPanel,
            AK.NSBackingStoreBuffered, False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(AK.NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setLevel_(AK.NSFloatingWindowLevel)
        self.panel.setCollectionBehavior_(AK.NSWindowCollectionBehaviorCanJoinAllSpaces
                                          | AK.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setTitle_("haochen · 状态入口")
        self.button = Button.alloc().initWithFrame_(AK.NSMakeRect(0, 0, 180, 36))
        self.button.owner = owner
        self.button.activity = Activity()
        self.button.pulse = 1.0
        self.button.attached = False
        self.button.notch_inset = 0.0
        self.button.setBordered_(False)
        self.button.setTitle_("")
        self.button.setTarget_(self.button)
        self.button.setAction_("invoke:")
        self.button.setButtonType_(AK.NSButtonTypeMomentaryPushIn)
        self.panel.setContentView_(self.button)
        self.button.entrance = self          # hover 跟踪回调落点
        self._owner = owner                  # QObject 父对象（动画生命周期）
        self._base_frame: tuple | None = None
        self._attached = False
        self._hover_state = "idle"
        self._hover_anim = None
        self._hover_generation = 0
        self._transition = None
        self._transition_generation = 0
        self.animation = QVariantAnimation(owner)
        self.animation.setStartValue(0.35)
        self.animation.setKeyValueAt(0.5, 1.0)
        self.animation.setEndValue(0.35)
        self.animation.setDuration(2200)
        self.animation.setLoopCount(-1)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.animation.valueChanged.connect(self._pulse)
        self.reduced_motion = False
        self.set_activity({})

    def _pulse(self, value):
        # 只重绘状态点的小矩形：不再 60fps 重绘整个胶囊（文字重光栅化会闪）。
        import AppKit as AK

        self.button.pulse = value
        bounds = self.button.bounds()
        rect = pulse_dirty_rect(bounds.size.width, bounds.size.height,
                                self.button.attached, self.button.notch_inset,
                                self.button.isFlipped(), getattr(self.button, "idle_band", None))
        self.button.setNeedsDisplayInRect_(AK.NSMakeRect(*rect))

    def _sync_animation(self):
        from PyQt6.QtCore import QAbstractAnimation

        running = self.animation.state() == QAbstractAnimation.State.Running
        # Pulse not only while working, but also when something needs the user:
        # @-mentions / approvals (attention) and connection errors breathe so the
        # entrance actively reminds instead of sitting as a static coloured dot.
        should_animate = (self.panel.isVisible() and not self.reduced_motion
                          and self.button.activity.kind in ("running", "attention", "error"))
        if should_animate and not running:
            self.animation.start()
        elif not should_animate:
            if running:
                self.animation.stop()
            self._pulse(1.0)

    def set_activity(self, data):
        activity = activity_from(data)
        changed = activity != self.button.activity
        if changed:
            self._start_transition()  # 用旧状态做交叉淡化起点（先捕获，再覆盖）
        self.button.activity = activity
        self.button.setAccessibilityLabel_(activity.accessible_label)
        self.button.setToolTip_(activity.accessible_label + "\n⌘M 收回总览；也可通过桌宠右键打开")
        if changed:
            self.button.setNeedsDisplay_(True)
            self._sync_animation()

    def _start_transition(self) -> None:
        """状态切换的交叉淡化：180ms 内新旧文案/颜色混合，reduced motion 直切。"""
        from PyQt6.QtCore import QVariantAnimation

        duration = transition_duration(self.reduced_motion)
        self._transition_generation += 1
        generation = self._transition_generation
        if self._transition is not None:
            self._transition.stop()
            self._transition.deleteLater()
            self._transition = None
        if duration <= 0:
            self.button.previous_activity = None
            self.button.blend = 1.0
            return
        self.button.previous_activity = self.button.activity
        self.button.blend = 0.0
        anim = QVariantAnimation(self._owner)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(duration * 1000))

        def step(value):
            if generation != self._transition_generation:
                return
            self.button.blend = value
            self.button.setNeedsDisplay_(True)

        def finish():
            if generation != self._transition_generation:
                return
            self.button.blend = 1.0
            self.button.previous_activity = None
            self.button.setNeedsDisplay_(True)

        anim.valueChanged.connect(step)
        anim.finished.connect(finish)
        self._transition = anim
        anim.start()

    # ── hover 弹性展开（Dynamic Island 手感）────────────────────

    def hover(self, entered: bool) -> None:
        """鼠标悬停进出入口：展开/收起，全部可中断续接。"""
        if self._base_frame is None or not self.panel.isVisible():
            return
        new_state = hover_next(self._hover_state, "enter" if entered else "exit")
        if new_state == self._hover_state:
            return
        self._hover_state = new_state
        if new_state == "hover_enter":
            self._animate_hover(True)
        elif new_state == "hover_exit":
            self._animate_hover(False)

    def _hover_target(self) -> tuple:
        base = self._base_frame
        if self._attached:
            # 贴合刘海：顶部带严格留在缺口内，宽度不变、只向下生长（不遮菜单栏）。
            min_x, max_x = base[0], base[0] + base[2]
        else:
            frame = self._owner._ns_screen().visibleFrame()
            min_x, max_x = frame.origin.x + 8, frame.origin.x + frame.size.width - 8
        return hover_frame(base, self._attached, min_x=min_x, max_x=max_x)

    def _animate_hover(self, expanding: bool) -> None:
        """弹簧插值收/展；从当前 frame 出发，中途反向连续续接（generation 守卫）。"""
        import AppKit as AK
        from PyQt6.QtCore import QEasingCurve, QVariantAnimation

        self._hover_generation += 1
        generation = self._hover_generation
        if self._hover_anim is not None:
            self._hover_anim.stop()
            self._hover_anim.deleteLater()
        current = self.panel.frame()
        start = (current.origin.x, current.origin.y, current.size.width, current.size.height)
        target = self._hover_target() if expanding else self._base_frame
        duration = hover_duration(self.reduced_motion)
        anim = QVariantAnimation(self._owner)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(duration * 1000))
        # 时间线性，弹性由 spring_value 提供；可中断反向时从当前帧重新出发。
        anim.setEasingCurve(QEasingCurve.Type.Linear)

        def step(value):
            if generation != self._hover_generation:
                return
            k = spring_value(value)
            frame = interpolate_frame(start, target, k)
            self.panel.setFrame_display_(AK.NSMakeRect(*frame), True)
            self.button.setFrame_(AK.NSMakeRect(0, 0, frame[2], frame[3]))
            self.button.hint_alpha = k if expanding else 1.0 - k
            self.button.setNeedsDisplay_(True)

        def finish():
            if generation != self._hover_generation:
                return
            frame = target
            self.panel.setFrame_display_(AK.NSMakeRect(*frame), True)
            self.button.setFrame_(AK.NSMakeRect(0, 0, frame[2], frame[3]))
            self.button.hint_alpha = 1.0 if expanding else 0.0
            self.button.setNeedsDisplay_(True)
            self._hover_state = hover_next(self._hover_state, "anim_done")

        anim.valueChanged.connect(step)
        anim.finished.connect(finish)
        self._hover_anim = anim
        anim.start()

    def place(self, frame, attached, reduced=False, notch_inset=0.0):
        import AppKit as AK

        self._base_frame = tuple(frame)
        self._attached = attached
        self.panel.setFrame_display_(AK.NSMakeRect(*frame), True)
        self.button.setFrame_(AK.NSMakeRect(0, 0, frame[2], frame[3]))
        self.button.attached = attached
        self.button.notch_inset = float(notch_inset) if attached else 0.0
        # 内容带的收起态高度：drawRect 顶部锚定的基准，hover 展开时保持内容不漂移。
        self.button.idle_band = frame[3] - (float(notch_inset) if attached else 0.0)
        self.button.setNeedsDisplay_(True)
        self.reduced_motion = reduced
        if self._hover_state in ("hover_enter", "expanded"):
            # 几何基准变了（屏幕/设置变更）：展开中的入口直接贴到新目标，不播旧动画。
            target = self._hover_target()
            self.panel.setFrame_display_(AK.NSMakeRect(*target), True)
            self.button.setFrame_(AK.NSMakeRect(0, 0, target[2], target[3]))
        self._sync_animation()

    def show(self):
        self.panel.orderFrontRegardless()
        self._sync_animation()

    def hide(self):
        # 隐藏时收起可能悬停展开的胶囊，下次出现从干净的收起态开始。
        self._hover_generation += 1
        if self._hover_anim is not None:
            self._hover_anim.stop()
            self._hover_anim = None
        self._hover_state = "idle"
        self.button.hint_alpha = 0.0
        if self._base_frame is not None:
            import AppKit as AK

            self.panel.setFrame_display_(AK.NSMakeRect(*self._base_frame), True)
            self.button.setFrame_(AK.NSMakeRect(0, 0, self._base_frame[2], self._base_frame[3]))
        self.panel.orderOut_(None)
        self._sync_animation()

    def close(self):
        self._hover_generation += 1
        self._transition_generation += 1
        if self._hover_anim is not None:
            self._hover_anim.stop()
        if self._transition is not None:
            self._transition.stop()
        self.animation.stop()
        self.button.disconnect_owner()
        self.button.setTarget_(None)
        self.panel.orderOut_(None)
        self.panel.close()
