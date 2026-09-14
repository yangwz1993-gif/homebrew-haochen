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
            palette = {"attention": (1, 0.76, 0.39), "error": (1, 0.48, 0.42),
                       "new": (0.70, 0.89, 0.73), "running": (0.65, 0.77, 0.98),
                       "idle": (0.65, 0.72, 0.67)}
            activity = self.activity
            red, green, blue = palette[activity.kind]
            # When attached the top `notch_inset` points sit under the physical
            # notch/camera, so the label/dot are centered in the band below it.
            inset = self.notch_inset if self.attached else 0.0
            # Center the label/dot in the band BELOW the notch. The notch occupies
            # the top `inset` points; where "top" is depends on the flip:
            #   flipped (y=0 at top): band is [inset, height]  -> center inset + b/2
            #   non-flipped (y=0 bottom): band is [0, height-inset] -> center b/2
            band = height - inset
            center_y = (inset + band / 2) if self.isFlipped() else (band / 2)
            AK.NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, self.pulse).setFill()
            AK.NSBezierPath.bezierPathWithOvalInRect_(AK.NSMakeRect(15, center_y - 3, 6, 6)).fill()
            paragraph = AK.NSMutableParagraphStyle.alloc().init()
            paragraph.setLineBreakMode_(AK.NSLineBreakByTruncatingTail)
            attrs = {AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_weight_(13, AK.NSFontWeightSemibold),
                     AK.NSForegroundColorAttributeName: AK.NSColor.colorWithCalibratedWhite_alpha_(0.96, 1),
                     AK.NSParagraphStyleAttributeName: paragraph}
            badge = "99+" if activity.count > 99 else str(activity.count) if activity.count else ""
            reserve = 30 if badge else 12
            AK.NSString.stringWithString_(activity.label).drawInRect_withAttributes_(
                AK.NSMakeRect(30, center_y - 9, width - 30 - reserve, 18), attrs)
            if badge:
                attrs[AK.NSForegroundColorAttributeName] = AK.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    red, green, blue, 1)
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
        self.button.activity = activity
        self.button.setAccessibilityLabel_(activity.accessible_label)
        self.button.setToolTip_(activity.accessible_label + "\n⌘M 收回总览；也可通过桌宠右键打开")
        if changed:
            self.button.setNeedsDisplay_(True)
            self._sync_animation()

    def place(self, frame, attached, reduced=False, notch_inset=0.0):
        import AppKit as AK

        self.panel.setFrame_display_(AK.NSMakeRect(*frame), True)
        self.button.setFrame_(AK.NSMakeRect(0, 0, frame[2], frame[3]))
        self.button.attached = attached
        self.button.notch_inset = float(notch_inset) if attached else 0.0
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
