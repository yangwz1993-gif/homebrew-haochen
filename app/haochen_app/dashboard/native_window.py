"""Bounded AppKit panel hosting bundled WebKit, on Qt's existing event loop.

No transparent full-screen catcher, second NSApplication loop, remote web
navigation, private WebKit preferences or Accessibility permission is needed.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QObject, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QPushButton


def _native_classes():
    import AppKit as AK
    import objc
    import WebKit as WK
    from Foundation import NSURL, NSJSONSerialization, NSObject

    class HaochenOverviewPanel(AK.NSPanel):
        def canBecomeKeyWindow(self):
            return True

        def canBecomeMainWindow(self):
            return False

    class HaochenOverviewHandler(NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler"),
                                                     objc.protocolNamed("WKNavigationDelegate")]):
        def userContentController_didReceiveScriptMessage_(self, _controller, message):
            if self.owner is None or not message.frameInfo().isMainFrame():
                return
            url = message.frameInfo().request().URL()
            if url is None or not url.isFileURL() or str(url.path()) != str(self.owner.document):
                return
            data, _error = NSJSONSerialization.dataWithJSONObject_options_error_(message.body(), 0, None)
            if data is None or data.length() > 65536:
                return
            try:
                # A verified main-frame script can say ready before WebKit's
                # didFinishNavigation callback. Replies must already be allowed
                # here, otherwise the frontend's request times out after 20s.
                self.owner.loaded = True
                self.owner.message.emit(json.loads(bytes(data)))
            except (ValueError, TypeError):
                return

        def webView_decidePolicyForNavigationAction_preferences_decisionHandler_(
            self, _webview, action, preferences, decide
        ):
            url = action.request().URL()
            permitted = (self.owner is not None and url is not None and url.isFileURL()
                         and str(url.path()) == str(self.owner.document))
            decide(WK.WKNavigationActionPolicyAllow if permitted else WK.WKNavigationActionPolicyCancel, preferences)
            if not permitted and self.owner is not None:
                self.owner.navigation_denied.emit()

        def webView_didFinishNavigation_(self, _webview, _navigation):
            if self.owner is not None:
                self.owner.loaded = True
                self.owner.evaluate("window.haochenVisibilityChanged?.(" +
                                    str(self.owner.expanded).lower() + ")")
                self.owner.ready.emit()

        def webViewWebContentProcessDidTerminate_(self, webview):
            if self.owner is not None and not self.owner.closed:
                self.owner.loaded = False
                webview.reload()

    # PyObjC 12.2.2 can create the initial subclass IMP before resolving inherited
    # WebKit callable metadata. The selector's __metadata__ then looks correct,
    # but WebKit's non-introspectable decision block still arrives untyped and
    # calling it aborts the process. Rebind AFTER the class exists via PyObjC's
    # public API, so its actual IMP is built using the resolved block metadata.
    # This preserves WebKit's exactly-once completion and our navigation policy.
    policy_method = HaochenOverviewHandler.webView_decidePolicyForNavigationAction_preferences_decisionHandler_
    objc.classAddMethods(HaochenOverviewHandler, [objc.selector(
        policy_method.callable, selector=policy_method.selector, signature=policy_method.signature)])

    class HaochenOverviewWebView(WK.WKWebView):
        def cancelOperation_(self, sender):
            if self.hasMarkedText():
                objc.super(HaochenOverviewWebView, self).cancelOperation_(sender)
            elif self.owner is not None:
                self.owner.evaluate("window.haochenNativeEscape?.()")

        def draggingEntered_(self, sender):
            return self.draggingUpdated_(sender)

        def draggingUpdated_(self, sender):
            urls = sender.draggingPasteboard().readObjectsForClasses_options_(
                [NSURL], {AK.NSPasteboardURLReadingFileURLsOnlyKey: True})
            if self.owner is not None and urls:
                self.owner.evaluate("window.haochenDropChanged?.(true)")
                return AK.NSDragOperationCopy
            return AK.NSDragOperationNone

        def draggingExited_(self, _sender):
            if self.owner is not None:
                self.owner.evaluate("window.haochenDropChanged?.(false)")

        def prepareForDragOperation_(self, _sender):
            return True

        def performDragOperation_(self, sender):
            if self.owner is None:
                return False
            urls = sender.draggingPasteboard().readObjectsForClasses_options_(
                [NSURL], {AK.NSPasteboardURLReadingFileURLsOnlyKey: True}) or []
            paths = [str(url.path()) for url in urls if url.isFileURL()][:30]
            self.owner.evaluate("window.haochenDropChanged?.(false)")
            if paths:
                self.owner.files_dropped.emit(paths)
                return True
            return False

        @objc.python_method
        def disconnect_owner(self):
            self.owner = None

    return HaochenOverviewPanel, HaochenOverviewHandler, HaochenOverviewWebView


_CLASSES = None


class NativeDashboard(QObject):
    message = pyqtSignal(dict)
    files_dropped = pyqtSignal(list)
    ready = pyqtSignal()
    visibility_changed = pyqtSignal(bool)
    summon_requested = pyqtSignal()
    navigation_denied = pyqtSignal()

    def __init__(self, assets: Path, parent=None):
        super().__init__(parent)
        import AppKit as AK
        import WebKit as WK
        from Foundation import NSURL

        global _CLASSES
        if _CLASSES is None:
            _CLASSES = _native_classes()
        Panel, Handler, WebView = _CLASSES
        self.document = (assets / "index.html").resolve(strict=True)
        self.loaded = False
        self.closed = False
        self.expanded = False
        self.settings = {}
        self._animation = None
        self._generation = 0
        self.handler = Handler.alloc().init()
        self.handler.owner = self
        config = WK.WKWebViewConfiguration.alloc().init()
        config.setWebsiteDataStore_(WK.WKWebsiteDataStore.nonPersistentDataStore())
        config.userContentController().addScriptMessageHandler_name_(self.handler, "haochen")
        self.webview = WebView.alloc().initWithFrame_configuration_(AK.NSMakeRect(0, 0, 1020, 700), config)
        self.webview.owner = self
        self.webview.setNavigationDelegate_(self.handler)
        self.webview.setAllowsBackForwardNavigationGestures_(False)
        self.webview.registerForDraggedTypes_([AK.NSPasteboardTypeFileURL])
        self.webview.setWantsLayer_(True)
        self.webview.layer().setCornerRadius_(28)
        self.webview.layer().setMasksToBounds_(True)
        self._content_size = (1020, 700)
        self.webview.setAutoresizingMask_(AK.NSViewNotSizable)
        # Resize only this clipping viewport during the liquid transition. The
        # web document retains its final size, so Chinese text never rewraps at
        # every animation tick or briefly falls into the mobile layout.
        self.viewport = AK.NSView.alloc().initWithFrame_(AK.NSMakeRect(0, 0, 1020, 700))
        self.viewport.setAutoresizesSubviews_(False)
        self.viewport.setWantsLayer_(True)
        self.viewport.layer().setCornerRadius_(28)
        self.viewport.layer().setMasksToBounds_(True)
        self.viewport.addSubview_(self.webview)
        self.panel = Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            AK.NSMakeRect(100, 100, 1020, 700), AK.NSWindowStyleMaskBorderless,
            AK.NSBackingStoreBuffered, False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(AK.NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setLevel_(AK.NSFloatingWindowLevel)
        self.panel.setCollectionBehavior_(AK.NSWindowCollectionBehaviorCanJoinAllSpaces
                                          | AK.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setTitle_("haochen · 桌面总览")
        self.panel.setContentView_(self.viewport)
        self.handle = QPushButton("h  ·")
        self.handle.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                   | Qt.WindowType.WindowStaysOnTopHint)
        self.handle.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.handle.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.handle.setAccessibleName("打开 haochen 桌面总览")
        self.handle.setToolTip("haochen · 桌面总览\n也可通过桌宠右键打开")
        self.handle.setStyleSheet("QPushButton{background:#eaf0e7;color:#334638;border:1px solid #b6c4b2;"
                                 "border-radius:17px;font:20px Georgia;padding:4px;}"
                                 "QPushButton:hover{background:#f8faf5;}")
        self.handle.clicked.connect(self.summon_requested)
        app = QApplication.instance()
        app.screenRemoved.connect(self._screens_changed)
        app.screenAdded.connect(self._screens_changed)
        self.webview.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(str(self.document)), NSURL.fileURLWithPath_(str(assets.resolve())))

    def _screens_changed(self, *_args):
        if self.closed:
            return
        self._place_handle()
        if self.expanded:
            self._generation += 1
            if self._animation:
                self._animation.stop()
            target = self._open_frame()
            self._size_webview(target)
            self._set_frame(target)
            self.panel.setAlphaValue_(1)

    def _screen(self):
        return QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()

    def _place_handle(self):
        rect = self._screen().availableGeometry()
        if self.settings.get("dock") == "notch":
            self.handle.setFixedSize(74, 34)
            # Below visibleFrame: never covers the hardware notch or macOS menus.
            self.handle.move(rect.center().x() - 37, rect.top() + 4)
        else:
            self.handle.setFixedSize(46, 66)
            self.handle.move(rect.right() - 51, rect.top() + rect.height() // 3)

    def start(self):
        self._place_handle()
        self.handle.show()

    def set_settings(self, settings):
        self.settings = dict(settings)
        self._place_handle()

    def _ns_screen(self):
        import AppKit as AK
        mouse = AK.NSEvent.mouseLocation()
        for screen in AK.NSScreen.screens():
            if AK.NSPointInRect(mouse, screen.frame()):
                return screen
        return AK.NSScreen.mainScreen() or AK.NSScreen.screens()[0]

    def _open_frame(self):
        area = self._ns_screen().visibleFrame()
        width, height = min(1040, area.size.width - 48), min(740, area.size.height - 44)
        return (area.origin.x + (area.size.width - width) / 2,
                area.origin.y + (area.size.height - height) / 2, width, height)

    def _closed_frame(self):
        area = self._ns_screen().visibleFrame()
        if self.settings.get("dock") == "notch":
            return (area.origin.x + area.size.width / 2 - 37,
                    area.origin.y + area.size.height - 38, 74, 34)
        return (area.origin.x + area.size.width - 52,
                area.origin.y + area.size.height * 2 / 3 - 66, 46, 66)

    def _set_frame(self, frame):
        import AppKit as AK
        self.panel.setFrame_display_(AK.NSMakeRect(*frame), True)
        width, height = self._content_size
        self.webview.setFrameOrigin_(AK.NSMakePoint((frame[2] - width) / 2, (frame[3] - height) / 2))

    def _size_webview(self, frame):
        import AppKit as AK
        size = (frame[2], frame[3])
        if size != self._content_size:
            self._content_size = size
            self.webview.setFrameSize_(AK.NSMakeSize(*size))

    def _animate(self, target, opening):
        import AppKit as AK
        self._generation += 1
        generation = self._generation
        if self._animation:
            self._animation.stop()
            self._animation.deleteLater()
        current = self.panel.frame()
        start = (current.origin.x, current.origin.y, current.size.width, current.size.height)
        start_alpha = self.panel.alphaValue()
        reduced = self.settings.get("motion") == "reduced" or (
            AK.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(0 if reduced else (400 if opening else 300))
        animation.setEasingCurve(QEasingCurve.Type.OutCubic if opening else QEasingCurve.Type.InOutCubic)

        def step(value):
            if self.closed or generation != self._generation:
                return
            self._set_frame(tuple(a + (b - a) * value for a, b in zip(start, target, strict=True)))
            self.panel.setAlphaValue_(start_alpha + ((1.0 if opening else 0.0) - start_alpha) * value)

        def finish():
            if self.closed or generation != self._generation:
                return
            if not opening:
                self.panel.orderOut_(None)
                self.handle.show()
            else:
                self.panel.setAlphaValue_(1)

        animation.valueChanged.connect(step)
        animation.finished.connect(finish)
        self._animation = animation
        animation.start()

    def show(self):
        import AppKit as AK
        if self.closed:
            return
        if not self.expanded:
            target = self._open_frame()
            self._size_webview(target)
            # A quick reversal resumes the current frame/opacity, without
            # teleporting back to the handle while the closing window is visible.
            if not self.panel.isVisible():
                self._set_frame(self._closed_frame())
                self.panel.setAlphaValue_(0)
            self.expanded = True
            self.handle.hide()
            self.panel.makeKeyAndOrderFront_(None)
            self.panel.makeFirstResponder_(self.webview)
            AK.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            self._animate(target, True)
            self.evaluate("window.haochenVisibilityChanged?.(true)")
            self.visibility_changed.emit(True)
        else:
            self.panel.makeKeyAndOrderFront_(None)

    def hide(self):
        if self.closed or not self.expanded:
            return
        self.expanded = False
        self.evaluate("window.haochenVisibilityChanged?.(false)")
        self._place_handle()
        self._animate(self._closed_frame(), False)
        self.visibility_changed.emit(False)

    def evaluate(self, script):
        if not self.closed and self.loaded:
            self.webview.evaluateJavaScript_completionHandler_(script, None)

    def send(self, payload):
        self.evaluate("window.haochenReceive?.(" + json.dumps(payload, ensure_ascii=True) + ")")

    def stop(self):
        if self.closed:
            return
        self.closed = True
        self._generation += 1
        if self._animation:
            self._animation.stop()
        self.handle.close()
        self.handle.deleteLater()
        self.webview.stopLoading()
        self.webview.configuration().userContentController().removeScriptMessageHandlerForName_("haochen")
        self.webview.setNavigationDelegate_(None)
        self.webview.disconnect_owner()
        self.handler.owner = None
        self.panel.orderOut_(None)
        self.panel.close()
