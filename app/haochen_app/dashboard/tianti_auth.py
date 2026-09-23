"""天梯日报的内嵌登录窗（零插件的 cookie 傻瓜通道）。

弹一个 WKWebView 小窗打开 cowork 日报页；用户在内网 SSO 登录一次（扫码/账密随他），
SSO 会话落地后从 WKWebsiteDataStore 原生 cookie 仓抓整套域 cookie（HttpOnly 也读得到，
document.cookie 做不到这点），拼成完整 Cookie 头存进 Keychain，自动关窗。

线程模型（2026-09-17 崩溃教训，SIGABRT 实锤）：WebKit 的 cookie 完成回调跑在 WebKit 的
IPC 线程上，绝不能在里面碰 AppKit/Qt——回调里只发 Qt 信号，所有收尾操作经
QueuedConnection 回到主线程执行。
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

log = logging.getLogger("haochen.tianti.auth")

LOGIN_URL = "https://cowork.xiaohongshu.com/s/teach-2-v3/#daily"
_LOGIN_MARK = "common-internal-access-token-prod"  # SSO 会话落地的标志 cookie（出现即已登录）
_COOKIE_DOMAIN_SUFFIX = "xiaohongshu.com"
_POLL_MS = 1000
_TIMEOUT_MS = 5 * 60 * 1000  # 5 分钟没登录成功就自动关窗，不挂着吓人


def cookie_header_from(jar) -> str:
    """把 xiaohongshu 域的 cookie 罐拼成完整 Cookie 头（浏览器怎么发我们就怎么发——
    实测服务器要整套会话 cookie，单个 token 会被拒）。只含域名匹配项，顺序稳定。"""
    pairs = sorted(
        (str(c.name()), str(c.value()))
        for c in jar
        if str(c.domain()).endswith(_COOKIE_DOMAIN_SUFFIX)
    )
    return "; ".join(f"{name}={value}" for name, value in pairs if name and value)


class _CookieBridge(QObject):
    """WebKit IPC 线程 → Qt 主线程的安全通道（线程间只走信号，不共享调用）。"""

    cookies = pyqtSignal(object)


class TiantiLoginWindow:
    """一次性登录窗：取到 cookie 或用户关窗即结束。on_result(cookie_header_or_None)。"""

    def __init__(self, on_result):
        self._on_result = on_result
        self._window = None
        self._webview = None
        self._timer = None
        self._bridge = _CookieBridge()
        self._bridge.cookies.connect(self._on_cookies, Qt.ConnectionType.QueuedConnection)
        self._poll_ticks = 0
        self._deadline_checks = _TIMEOUT_MS // _POLL_MS
        self._done = False

    def show(self) -> None:
        import AppKit as AK
        import WebKit as WK

        frame = AK.NSMakeRect(0, 0, 520, 680)
        self._window = AK.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            AK.NSWindowStyleMaskTitled | AK.NSWindowStyleMaskClosable | AK.NSWindowStyleMaskResizable,
            AK.NSBackingStoreBuffered, False,
        )
        self._window.setTitle_("连接天梯日报 · 登录一次即可")
        self._window.center()
        config = WK.WKWebViewConfiguration.alloc().init()
        self._webview = WK.WKWebView.alloc().initWithFrame_configuration_(
            AK.NSMakeRect(0, 0, 520, 680), config)
        self._webview.setAutoresizingMask_(AK.NSViewWidthSizable | AK.NSViewHeightSizable)
        self._window.contentView().addSubview_(self._webview)
        # 先清掉 cookie 仓里残留的旧会话 cookie：否则轮询会在用户重新登录之前就
        # 秒抓旧值——只有这次登录产生的新一套才算数。
        self._purge_stale_cookies()
        self._webview.loadRequest_(
            WK.NSURLRequest.requestWithURL_(AK.NSURL.URLWithString_(LOGIN_URL)))
        # 用户直接关窗 = 取消
        self._window.setReleasedWhenClosed_(False)
        self._window.center()
        self._window.makeKeyAndOrderFront_(None)
        AK.NSApp.activateIgnoringOtherApps_(True)

        self._timer = QTimer()
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll_cookie)
        self._timer.start()

    # ── WebKit 侧（跑在 WebKit IPC 线程，只做信号转发，不碰 AppKit/Qt 状态）─────

    def _purge_stale_cookies(self) -> None:
        import WebKit as WK
        store = WK.WKWebsiteDataStore.defaultDataStore().httpCookieStore()
        store.getAllCookies_(self._purge_callback)  # 回调在 WebKit 线程：里面只删 cookie（IPC 代理调用，线程安全）

    def _purge_callback(self, cookies) -> None:
        import WebKit as WK
        store = WK.WKWebsiteDataStore.defaultDataStore().httpCookieStore()
        for cookie in cookies or []:
            if str(cookie.domain()).endswith(_COOKIE_DOMAIN_SUFFIX):
                # 选择器是 deleteCookie:completionHandler:；传 None 即不带完成回调
                store.deleteCookie_completionHandler_(cookie, None)

    def _poll_cookie(self) -> None:
        """主线程 QTimer：只做窗口状态判断，取 cookie 的事让 WebKit 回调经信号回来。"""
        if self._done:
            return
        if self._window is None or not self._window.isVisible():
            self._finish(None)  # 用户关窗 = 取消
            return
        self._deadline_checks -= 1
        if self._deadline_checks <= 0:
            self._finish(None)
            return
        import WebKit as WK
        store = WK.WKWebsiteDataStore.defaultDataStore().httpCookieStore()
        store.getAllCookies_(self._cookies_callback)

    def _cookies_callback(self, cookies) -> None:
        """WebKit IPC 线程上：只发信号，别的什么都不做。"""
        try:
            self._bridge.cookies.emit(list(cookies or []))
        except Exception:  # noqa: BLE001 - 窗口已销毁时信号发送可能失败
            pass

    # ── Qt 主线程侧（信号槽，收尾全在这里）─────────────────────────────

    def _on_cookies(self, cookies: list) -> None:
        if self._done:
            return
        jar = [c for c in cookies if str(c.domain()).endswith(_COOKIE_DOMAIN_SUFFIX)]
        self._poll_ticks += 1
        if self._poll_ticks % 10 == 1:  # 每 10s 一条，只看名字不看值
            log.info("tianti_auth poll: xiaohongshu 域 cookie 名 %s",
                     sorted({str(c.name()) for c in jar}) or "（一个都没有）")
        if any(str(c.name()) == _LOGIN_MARK and str(c.value() or "").strip() for c in jar):
            header = cookie_header_from(jar)
            if header:
                self._finish(header)

    def _finish(self, cookie_value) -> None:
        if self._done:
            return
        self._done = True
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        window, self._window = self._window, None
        if window is not None:
            window.close()
        try:
            self._on_result(cookie_value)
        except Exception:  # noqa: BLE001 - 回调失败不影响窗口已关的事实
            log.exception("tianti login callback failed")
