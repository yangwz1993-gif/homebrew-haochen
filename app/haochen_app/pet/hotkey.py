"""全局热键 ⌃⌥P（Ctrl+Option+P）：CGEventTap 实现，沿用上一版 pet.py 的方案。

依赖 pyobjc（Quartz/CoreFoundation）+ macOS「辅助功能」权限。
本模块**可选**：任一条件不满足（未装 pyobjc / 无权限 / Tap 创建失败）则返回 None，
调用方降级为「仅双击唤起」，并在 UI/README 说明。P4/P5 打包后 pyobjc 随包内置。
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer

HOTKEY_LABEL = "⌃⌥P"
_HOTKEY_KEYCODE = 35  # kVK_ANSI_P


def install_hotkey(callback) -> tuple[bool, str]:
    """安装全局热键。成功返回 (True, 说明)；失败返回 (False, 降级原因)。"""
    try:
        import CoreFoundation
        import Quartz
    except ImportError:
        return False, "未安装 pyobjc（开发 venv 无此依赖），全局热键不可用，降级为双击唤起"

    def _tap_cb(_proxy, etype, event, _refcon):
        if etype == Quartz.kCGEventTapDisabledByTimeout:
            Quartz.CGEventTapEnable(_tap_cb.tap, True)
            return event
        if etype == Quartz.kCGEventKeyDown:
            flags = Quartz.CGEventGetFlags(event)
            code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            need = Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskAlternate
            if code == _HOTKEY_KEYCODE and (flags & need) == need:
                QTimer.singleShot(0, callback)
        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
        _tap_cb,
        None,
    )
    if tap is None:
        return False, ("无「辅助功能」权限，全局热键不可用，降级为双击唤起"
                       "（系统设置 → 隐私与安全性 → 辅助功能 中授权后重试）")
    _tap_cb.tap = tap  # 防 GC + 供超时恢复引用
    src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    CoreFoundation.CFRunLoopAddSource(
        CoreFoundation.CFRunLoopGetCurrent(), src, CoreFoundation.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    return True, f"全局热键 {HOTKEY_LABEL} 已启用"
