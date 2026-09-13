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


def entrance_geometry(frame, visible, safe_top=0, left=None, right=None, dock="notch"):
    """Return (AppKit frame, physically attached).

    On a notched screen the entrance starts at the physical notch's lower edge,
    and is no wider than the public auxiliary-area gap. Its entire hit region is
    below the reserved top area. On other screens it is a normal top capsule.
    All coordinates stay in AppKit's global coordinate space, including screens
    placed to the left or above the primary display.
    """
    x, y, width, height = frame
    vx, vy, vw, vh = visible
    if dock == "side":
        return (vx + vw - 194, vy + vh * 2 / 3 - 40, 186, 40), False
    top = y + height
    safe_top = max(0, min(float(safe_top), 100))
    if safe_top > 0 and left and right:
        gap_left = max(x, left[0] + left[2])
        gap_right = min(x + width, right[0])
        gap = gap_right - gap_left
        if 100 <= gap <= min(400, width / 2):
            return (gap_left, top - safe_top - 36, gap, 36), True
    w = min(206, vw - 16)
    return (vx + (vw - w) / 2, min(top - safe_top, vy + vh) - 44, w, 38), False


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
        def drawRect_(self, _dirty):
            bounds = self.bounds()
            width, height = bounds.size.width, bounds.size.height
            path = AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 17, 17)
            if self.attached:
                # Square top edge joins the hardware notch. Only the lower
                # corners are rounded; no transparent catcher surrounds it.
                top_y = 0 if self.isFlipped() else height / 2
                path.appendBezierPathWithRect_(AK.NSMakeRect(0, top_y, width, height / 2))
            AK.NSColor.colorWithCalibratedWhite_alpha_(0.12 if self.isHighlighted() else 0.025, 1).setFill()
            path.fill()
            palette = {"attention": (1, 0.76, 0.39), "error": (1, 0.48, 0.42),
                       "new": (0.70, 0.89, 0.73), "running": (0.65, 0.77, 0.98),
                       "idle": (0.65, 0.72, 0.67)}
            activity = self.activity
            red, green, blue = palette[activity.kind]
            center_y = height / 2
            AK.NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, self.pulse).setFill()
            AK.NSBezierPath.bezierPathWithOvalInRect_(AK.NSMakeRect(15, center_y - 3, 6, 6)).fill()
            paragraph = AK.NSMutableParagraphStyle.alloc().init()
            paragraph.setLineBreakMode_(AK.NSLineBreakByTruncatingTail)
            attrs = {AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_weight_(12, AK.NSFontWeightMedium),
                     AK.NSForegroundColorAttributeName: AK.NSColor.colorWithCalibratedWhite_alpha_(0.92, 1),
                     AK.NSParagraphStyleAttributeName: paragraph}
            badge = "99+" if activity.count > 99 else str(activity.count) if activity.count else ""
            reserve = 30 if badge else 12
            AK.NSString.stringWithString_(activity.label).drawInRect_withAttributes_(
                AK.NSMakeRect(30, center_y - 8, width - 30 - reserve, 17), attrs)
            if badge:
                attrs[AK.NSForegroundColorAttributeName] = AK.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    red, green, blue, 1)
                attrs[AK.NSFontAttributeName] = AK.NSFont.monospacedDigitSystemFontOfSize_weight_(
                    11, AK.NSFontWeightSemibold)
                AK.NSString.stringWithString_(badge).drawInRect_withAttributes_(
                    AK.NSMakeRect(width - 30, center_y - 7, 26, 17), attrs)

        def invoke_(self, _sender):
            if self.owner is not None:
                self.owner.summon_requested.emit()

        @objc.python_method
        def disconnect_owner(self):
            self.owner = None

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
        self.button.setBordered_(False)
        self.button.setTitle_("")
        self.button.setTarget_(self.button)
        self.button.setAction_("invoke:")
        self.button.setButtonType_(AK.NSButtonTypeMomentaryPushIn)
        self.panel.setContentView_(self.button)
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
        self.button.pulse = value
        self.button.setNeedsDisplay_(True)

    def _sync_animation(self):
        from PyQt6.QtCore import QAbstractAnimation

        running = self.animation.state() == QAbstractAnimation.State.Running
        should_animate = self.panel.isVisible() and self.button.activity.kind == "running" and not self.reduced_motion
        if should_animate and not running:
            self.animation.start()
        elif not should_animate:
            if running:
                self.animation.stop()
            self._pulse(1.0)

    def set_activity(self, data):
        activity = activity_from(data)
        changed = activity != self.button.activity
        self.button.activity = activity
        self.button.setAccessibilityLabel_(activity.accessible_label)
        self.button.setToolTip_(activity.accessible_label + "\n⌘M 收回总览；也可通过桌宠右键打开")
        if changed:
            self.button.setNeedsDisplay_(True)
            self._sync_animation()

    def place(self, frame, attached, reduced=False):
        import AppKit as AK

        self.panel.setFrame_display_(AK.NSMakeRect(*frame), True)
        self.button.setFrame_(AK.NSMakeRect(0, 0, frame[2], frame[3]))
        self.button.attached = attached
        self.button.setNeedsDisplay_(True)
        self.reduced_motion = reduced
        self._sync_animation()

    def show(self):
        self.panel.orderFrontRegardless()
        self._sync_animation()

    def hide(self):
        self.panel.orderOut_(None)
        self._sync_animation()

    def close(self):
        self.animation.stop()
        self.button.disconnect_owner()
        self.button.setTarget_(None)
        self.panel.orderOut_(None)
        self.panel.close()
