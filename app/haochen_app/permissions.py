"""「辅助功能」权限检测与授权引导（DoD#5 / acceptance §0.1）。

macOS 把 AX 权限归到「责任进程」——生产形态即本 .app 自身。`AXIsProcessTrustedWithOptions`
正是在**调用进程的责任进程**维度判定，故此检测是准确的；未授权时返回 False（读屏不可用，
其余功能正常）。

已知平台行为：**ad-hoc 签名的 .app 每次重建签名（designated requirement）会变 → TCC 授权
不跨构建持久**。因此分布式构建须用稳定签名（Developer ID 或自签+信任），否则每次重建都要
重新授权。

引导策略（v0.1.4 §0）：不再弹产品自己的授权卡片——检测到未授权**直接触发 macOS 系统授权
弹窗**（系统弹窗只在首次未决时出现；已拒绝时系统不再弹，故同时打开对应设置面板引导手动
开启），随后用 QTimer 轮询授权状态，全部授权后提示重启（屏幕录制需重启生效）。

已确诊的顽固故障（v0.1.3 修复）：**签名身份重建/换证书后，设置列表里的旧开关仍是蓝的，
但 TCC 记录里的 designated requirement 与当前二进制对不上**——tccd 实际判定未授权
（AXIsProcessTrustedWithOptions=False，系统甚至重新弹授权框），且**重拨开关不会更新该记录**
（真机实测）。此时唯一出路是 `tccutil reset` 清掉本 app 的旧记录再重新授权。轮询超时仍未
授权时弹出「重置重授」提示走这条路；无需管理员，只清本 app 条目。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox, QWidget

log = logging.getLogger("haochen.permissions")

_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
_SETTINGS_SCREEN = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
_BUNDLE_ID_FALLBACK = "com.haochen.app"


def accessibility_granted() -> bool:
    """是否已持有辅助功能权限（按调用进程的责任进程判定，不触发系统弹窗）。"""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        v = bool(AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": False}))
        log.info("accessibility_granted -> %s", v)
        return v
    except Exception as exc:  # noqa: BLE001
        log.warning("accessibility check error: %s", exc, exc_info=True)
        return False


def screen_recording_granted() -> bool:
    """是否已获屏幕录制权限（视觉读屏用，P7）。"""
    try:
        from Quartz import CGPreflightScreenCaptureAccess
        v = bool(CGPreflightScreenCaptureAccess())
        log.info("screen_recording_granted -> %s", v)
        return v
    except Exception as exc:  # noqa: BLE001
        log.warning("screen recording check error: %s", exc, exc_info=True)
        return False


def request_accessibility() -> bool:
    """触发系统授权弹窗（app 进入辅助功能列表），返回当前状态。"""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        return bool(AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True}))
    except Exception:  # noqa: BLE001
        return False


def request_screen_recording() -> bool:
    """触发系统屏幕录制授权弹窗，返回当前状态。"""
    try:
        from Quartz import CGRequestScreenCaptureAccess
        return bool(CGRequestScreenCaptureAccess())
    except Exception:  # noqa: BLE001
        return False


def open_settings() -> None:
    subprocess.Popen(["open", _SETTINGS_URL])  # noqa: S603,S607


def _bundle_id() -> str:
    """当前 app 的 bundle id（tccutil 客户端标识）；非打包形态回退默认值。"""
    try:
        from Foundation import NSBundle
        bid = NSBundle.mainBundle().bundleIdentifier()
        if bid:
            return str(bid)
    except Exception:  # noqa: BLE001
        pass
    return _BUNDLE_ID_FALLBACK


def reset_tcc_records() -> bool:
    """重置本 app 的 TCC 记录（辅助功能 + 屏幕录制），用于清除「开关蓝但系统不认」的旧记录。

    只清本 app 条目、无需管理员。重置后须重新触发系统弹窗（request_*），新记录才会
    绑定当前二进制的 designated requirement。
    """
    bid = _bundle_id()
    ok = True
    for service in ("Accessibility", "ScreenCapture"):
        r = subprocess.run(["tccutil", "reset", service, bid],
                           capture_output=True, text=True)
        if r.returncode != 0:
            log.warning("tccutil reset %s %s failed rc=%s: %s",
                        service, bid, r.returncode, (r.stderr or r.stdout).strip())
            ok = False
    log.info("tcc records reset for %s ok=%s", bid, ok)
    return ok


def _reset_and_reprompt() -> None:
    """「重置重授」：清旧记录 → 重新弹系统授权框（绑定当前签名）→ 直达设置。"""
    reset_tcc_records()
    request_accessibility()
    request_screen_recording()
    open_settings()


# ── 构建指纹：签名证书变更 → 清历史 TCC 记录（v0.1.4 hotfix；v0.1.6 起仅按证书）────
#
# 背景：稳定签名使 designated requirement 跨构建固定，旧版本授过的 TCC 记录对
# 新构建依然有效——用户从未对本版授权却能读屏。故启动时比对签名证书指纹，
# 不一致（换证书/签名重建）即清历史授权记录重授。
# v0.1.6：指纹不再含版本号——TCC 授权绑定的是证书 DR，与 app 版本无关；
# 证书不变时升级不应清授权（避免每次升级都重新授权）。

_FINGERPRINT_FILE = "build-fingerprint"


def _signing_cert_fingerprint() -> str:
    """当前 app 签名 leaf 证书 SHA-1；非 frozen/取不到时回退固定串。"""
    from .app_signing_repair import app_bundle
    app = app_bundle()
    if app is None:
        return "unfrozen"
    try:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="haochen-cert-") as td:
            r = subprocess.run(
                ["codesign", "-d", f"--extract-certificates={td}/cert", str(app)],
                capture_output=True, text=True)
            leaf = Path(td) / "cert0"
            if r.returncode == 0 and leaf.exists():
                h = subprocess.run(["shasum", "-a", "1", str(leaf)],
                                   capture_output=True, text=True)
                fp = h.stdout.split()[0] if h.returncode == 0 else ""
                if fp:
                    return fp
    except Exception as exc:  # noqa: BLE001
        log.warning("signing cert fingerprint error: %s", exc)
    return "unknown"


def build_fingerprint() -> str:
    """构建指纹 = 签名证书指纹（v0.1.6 起不含版本号：证书不变 → 授权升级后仍有效）。"""
    return _signing_cert_fingerprint()


def _fingerprint_cert(fp: str) -> str:
    """从指纹串取证书部分（兼容 v0.1.5 的「版本|证书」格式）。"""
    return fp.rsplit("|", 1)[-1] if fp else ""


def reconcile_tcc_with_build(home: Path) -> bool:
    """构建指纹比对：签名证书与上次记录不一致 → 清历史 TCC 授权记录，再写入新指纹。

    一致（同一签名证书）则不动——证书不变时授权跨版本/重启持久，升级不再要求
    重新授权（v0.1.6）。清完记录后现有权限引导（_guide_permissions）自然会发现
    未授权并重走系统弹窗。返回是否发生了重置。mock/自动化场景由调用方跳过。
    """
    fp = build_fingerprint()
    fp_file = Path(home) / _FINGERPRINT_FILE
    try:
        prev = fp_file.read_text(encoding="utf-8").strip() if fp_file.exists() else ""
    except OSError:
        prev = ""
    if prev and _fingerprint_cert(prev) == fp:
        log.info("signing cert unchanged (%s), tcc records kept", fp)
        return False
    log.info("signing cert changed (%s -> %s): resetting tcc records",
             prev or "<none>", fp)
    reset_tcc_records()
    try:
        fp_file.parent.mkdir(parents=True, exist_ok=True)
        fp_file.write_text(fp, encoding="utf-8")
    except OSError as exc:
        log.warning("write build fingerprint failed: %s", exc)
    return True


# ── 授权引导：直接走系统弹窗 + 轮询（v0.1.4 §0）────────────────

_POLL_INTERVAL_MS = 1500   # 授权状态轮询间隔
_POLL_TIMEOUT_S = 90.0     # 超时后给出「重置重授」入口（假蓝场景）
_active_poll = None        # 持有进行中的 QTimer，防 GC


def relaunch_app() -> None:
    """重启本 app（屏幕录制授权后需重启生效）。

    打包形态：detached `sh -c 'sleep 1; open -n <app>'` 后退出（独立 session，
    不随父进程被杀）。开发形态（非 frozen）只记日志——不重启，避免搞死终端会话。
    """
    from .app_signing_repair import app_bundle

    app = app_bundle()
    if app is None:
        log.info("dev mode (not frozen): skip relaunch, please restart manually")
        return
    try:
        subprocess.Popen(["sh", "-c", f"sleep 1; open -n '{app}'"],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603,S607
    except Exception as exc:  # noqa: BLE001
        log.warning("relaunch schedule failed: %s", exc)
        return
    from PyQt6.QtWidgets import QApplication
    log.info("relaunch scheduled, quitting")
    QApplication.quit()


def ensure_permissions(parent: QWidget | None = None, on_all_granted=None) -> None:
    """直接触发系统授权；轮询检测授权完成；完成后回调并提示重启。

    未授权项逐个触发系统弹窗（系统弹窗只在首次未决时出现；已拒绝时系统不再弹，
    故同时打开对应设置面板引导手动开启）。全部授权 → on_all_granted() 回调，若屏幕
    录制是新授权的则提示重启；轮询超时仍未授权 → 弹「重置重授」提示（假蓝场景入口）。
    """
    need_ax = not accessibility_granted()
    need_sr = not screen_recording_granted()
    if not (need_ax or need_sr):
        if on_all_granted:
            on_all_granted()
        return
    if need_ax:
        request_accessibility()   # 系统弹窗（仅首次未决时出现）
        open_settings()           # 已拒绝时系统不再弹 → 直达设置面板手动开
    if need_sr:
        request_screen_recording()
        open_screen_settings(screen=True)
    _start_poll(parent, on_all_granted, need_restart=need_sr)


def _start_poll(parent: QWidget | None, on_all_granted, need_restart: bool) -> None:
    """每 1.5s 轮询授权状态：全部授权 → 完成提示；超时 → 重置重授提示。"""
    import time as _t

    from PyQt6.QtCore import QTimer

    global _active_poll
    timer = QTimer(parent)
    deadline = _t.monotonic() + _POLL_TIMEOUT_S

    def tick() -> None:
        if accessibility_granted() and screen_recording_granted():
            timer.stop()
            _on_granted(parent, on_all_granted, need_restart)
        elif _t.monotonic() > deadline:
            timer.stop()
            _prompt_reset(parent, on_all_granted, need_restart)

    timer.timeout.connect(tick)
    timer.start(_POLL_INTERVAL_MS)
    _active_poll = timer


def _on_granted(parent: QWidget | None, on_all_granted, need_restart: bool) -> None:
    """全部授权：回调；屏幕录制是新授权的 → 提示重启（屏幕录制需重启生效）。"""
    log.info("permissions all granted")
    if on_all_granted:
        on_all_granted()
    if not need_restart:
        return
    box = QMessageBox(parent)
    box.setWindowTitle("权限已就绪")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText("权限已就绪。屏幕录制需重启 haochen 生效。")
    btn_now = box.addButton("立即重启", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(btn_now)
    box.exec()
    if box.clickedButton() is btn_now:
        relaunch_app()


def _prompt_reset(parent: QWidget | None, on_all_granted, need_restart: bool) -> None:
    """轮询超时仍未授权：「假蓝」场景入口——重置重授（清旧记录 + 重弹系统授权）或暂不。"""
    log.info("permissions poll timeout: ax=%s sr=%s",
             accessibility_granted(), screen_recording_granted())
    box = QMessageBox(parent)
    box.setWindowTitle("授权未生效")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText("仍未检测到授权。若系统开关已开（蓝）却不生效，多半是授权记录与签名不符。")
    box.setInformativeText(
        "「重置重授」会清除本 app 的旧授权记录并重新弹出系统授权"
        "（无需管理员，仅清本 app 条目）。")
    btn_reset = box.addButton("重置重授", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("暂不", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is btn_reset:
        _reset_and_reprompt()
        _start_poll(parent, on_all_granted, need_restart)  # 重置后继续轮询


def open_screen_settings(screen: bool = False) -> None:
    """设置面板：辅助功能或屏幕录制。"""
    url = (_SETTINGS_SCREEN if screen else _SETTINGS_URL)
    subprocess.Popen(["open", url])  # noqa: S603,S607
