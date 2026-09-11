#!/usr/bin/env python3
"""haochen 读屏执行体（PyInstaller --onedir；--serve 复用进程）。

通过 macOS Accessibility API 读取前台窗口真实内容（文字 + 图片），不截图。
移植自 previous-version/haochen-app/haochen/reader.py + cli.py --json 路径，
去除旧项目依赖（llm/config/pet），图片下载改 stdlib urllib。

用法（与 app/ext/index.ts 的约定一致）：
    haochen-reader --json [--pid N] [--no-scroll] [--max-scrolls N]
    haochen-reader --check   仅自查权限：stdout 打 JSON 状态，退出码 0=已授权，2=未授权
退出码：0 成功；1 AX 错误；2 未授权辅助功能。
未授权时硬门控：--json 读取前自查 AXIsProcessTrustedWithOptions(prompt=False)，
未授权直接报错退出（码 2），绝不真正读屏。
输出：stdout 单行 JSON {app, window_title, stats, blocks:[{kind,text|url,alt,data_url}]}。
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import ipaddress
import json
import os
import select
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import AppKit
from ApplicationServices import (
    AXIsProcessTrustedWithOptions,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementSetAttributeValue,
    AXUIElementSetMessagingTimeout,
)
from CoreFoundation import CFDataCreateMutable, CFDataGetBytes, CFDataGetLength, CFRangeMake
from Quartz import (
    CGEventCreate,
    CGEventCreateScrollWheelEvent,
    CGEventGetLocation,
    CGEventPost,
    CGImageDestinationAddImage,
    CGImageDestinationCreateWithData,
    CGImageDestinationFinalize,
    CGPreflightScreenCaptureAccess,
    CGRectNull,
    CGWarpMouseCursorPosition,
    CGWindowListCopyWindowInfo,
    CGWindowListCreateImage,
    kCGHIDEventTap,
    kCGScrollEventUnitLine,
    kCGWindowImageBoundsIgnoreFraming,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionAll,
    kCGWindowListOptionIncludingWindow,
    kCGWindowListOptionOnScreenOnly,
)
from reader.document_source import IMAGE_SUFFIXES, DocumentSource
from reader.screen_target import document_id, fingerprint, matching_ax_window, web_area

TEXT_VALUE_ROLES = {"AXStaticText", "AXTextArea", "AXTextField", "AXHeading"}
TITLE_ROLES = {"AXButton", "AXLink", "AXCheckBox", "AXRadioButton", "AXTab", "AXCell"}
IMAGE_ROLES = {"AXImage"}
MAX_DEPTH = 60
AX_TIMEOUT = 2.0
MAX_IMAGES = 15
IMG_TIMEOUT = 5.0


@dataclass
class Block:
    kind: str  # "text" | "image"
    text: str = ""
    url: str | None = None
    alt: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    role: str = ""

    @property
    def area(self) -> float:
        return self.w * self.h if self.w and self.h else 0.0


@dataclass
class WindowContent:
    app_name: str = ""
    window_title: str = ""
    blocks: list[Block] = field(default_factory=list)
    window_bounds: tuple[float, float, float, float] | None = None
    scope: str = "窗口当前可访问内容；未自动滚动，不保证整篇文档完整"


class AXError(RuntimeError):
    pass


class TargetError(AXError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def check_accessibility(prompt: bool = True) -> bool:
    return bool(AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": prompt}))


def _copy(el, attr):
    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    if err != 0:
        return None
    return val


def _geom(el) -> tuple[float, float]:
    pos = _copy(el, "AXPosition")
    try:
        return float(pos.x()), float(pos.y())
    except Exception:
        try:
            from ApplicationServices import AXValueGetValue, kAXValueCGPointType
            ok, value = AXValueGetValue(pos, kAXValueCGPointType, None)
            if ok:
                return float(value.x), float(value.y)
        except Exception:
            pass
        return 0.0, 0.0


def _size(el) -> tuple[float, float]:
    """读取元素可见尺寸（AXSize）。"""
    size = _copy(el, "AXSize")
    try:
        return float(size.width()), float(size.height())
    except Exception:
        try:
            from ApplicationServices import AXValueGetValue, kAXValueCGSizeType
            ok, value = AXValueGetValue(size, kAXValueCGSizeType, None)
            if ok:
                return float(value.width), float(value.height)
        except Exception:
            pass
        return 0.0, 0.0


def _norm(text) -> str:
    if text is None:
        return ""
    return " ".join(str(text).split())


def _walk(el, depth: int, out: list[Block]) -> None:
    if depth > MAX_DEPTH:
        return
    role = _copy(el, "AXRole")
    if role is None:
        return
    if _copy(el, "AXSubrole") == "AXSecureTextField":
        return
    if role == "AXHeading":
        # Chromium's AXValue is the heading LEVEL (1/2/...), not its text.
        title = _norm(_copy(el, "AXTitle"))
        if title:
            x, y = _geom(el)
            out.append(Block("text", text=title, x=x, y=y, role="AXHeading"))
        else:
            children = []
            for child in _copy(el, "AXChildren") or []:
                _walk(child, depth + 1, children)
            for block in children:
                if block.kind == "text":
                    block.role = "AXHeading"
            out.extend(children)
        return

    if role in TEXT_VALUE_ROLES:
        text = _norm(_copy(el, "AXValue")) or _norm(_copy(el, "AXTitle"))
        if text:
            x, y = _geom(el)
            out.append(Block(kind="text", text=text, x=x, y=y, role=str(role)))
        return  # 文本节点不必再深入
    if role in TITLE_ROLES:
        text = _norm(_copy(el, "AXTitle")) or _norm(_copy(el, "AXDescription"))
        if text:
            x, y = _geom(el)
            link = str(_copy(el, "AXURL") or "") if role == "AXLink" else None
            out.append(Block(kind="text", text=text, x=x, y=y, role=str(role), url=link))
        # 链接/按钮里可能包图片，继续走子节点
    elif role in IMAGE_ROLES:
        url = _copy(el, "AXURL")
        url = str(url) if url else None
        alt = _norm(_copy(el, "AXDescription")) or _norm(_copy(el, "AXTitle"))
        # Canvas/unnamed images still need visual fallback.
        x, y = _geom(el)
        w, h = _size(el)
        out.append(Block(kind="image", url=url, alt=alt, x=x, y=y, w=w, h=h))

    children = _copy(el, "AXChildren")
    if not children:
        return
    for child in children:
        _walk(child, depth + 1, out)


def _dedup_sort(blocks: list[Block]) -> list[Block]:
    """按视觉位置排序，去内容完全相同的块。"""
    blocks.sort(key=lambda b: (b.y, b.x))
    seen: set[tuple] = set()
    out = []
    for b in blocks:
        key = (b.kind, b.text, b.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def frontmost_pid() -> tuple[int, str]:
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        raise AXError("无法获取前台应用")
    return app.processIdentifier(), str(app.localizedName())


def app_name_for_pid(pid: int) -> str:
    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app:
        return str(app.localizedName())
    return f"pid:{pid}"


def _topmost_foreign_app(exclude: set[int] | None = None) -> tuple[int, str] | None:
    """用户「最近浏览」的非 haochen 窗口所属 App。

    宠物被点成前台（前台=haochen）时，读它"身后"的用户窗口（如 Chrome）。
    选**最大**的 layer-0 普通窗口（用户主窗口），排除本进程与 haochen app 自身——比纯 z 序
    更抗系统/悬浮窗口（如 WindowManager、菜单残留）干扰。
    """
    own = os.getpid()
    exclude = exclude or set()
    best: tuple[int, float, str] | None = None  # (pid, area, name)
    wins = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, 0)
    for w in wins or []:
        if int(w.get("kCGWindowLayer", -1)) != 0:
            continue
        owner = int(w.get("kCGWindowOwnerPID", -1))
        if owner in exclude or owner == own:
            continue
        b = w.get("kCGWindowBounds", {})
        area = float(b.get("Width", 0)) * float(b.get("Height", 0))
        if best is None or area > best[1]:
            best = (owner, area, str(w.get("kCGWindowOwnerName", "")))
    return (best[0], best[2]) if best else None


def target_pid() -> tuple[int, str]:
    """阅读/截图目标：

    - 前台 App 不是 haochen（用户正用 Chrome 等）→ 读前台；
    - 前台是 haochen（用户点宠物/气泡）→ 读「用户最近浏览的非 haochen 窗口」（Chrome），
      优先用壳追踪的 sidecar（last-user-app.txt），其次最大普通窗口。
    haochen app pid 由壳经 HAOCHEN_APP_PID 传入（读者是独立进程，os.getpid()≠app pid）。
    """
    app_pid = _hap()
    pid, name = frontmost_pid()
    if app_pid is None or pid != app_pid:
        return pid, name
    # 前台是 haochen：优先读壳追踪的「用户最近非 haochen app」（sidecar）
    tracked = _sidecar_user_app()
    if tracked and tracked != os.getpid() and tracked != app_pid:
        if _has_screen_window(tracked):
            return tracked, app_name_for_pid(tracked)
    # 兜底：最大普通窗口
    foreign = _topmost_foreign_app(exclude={app_pid})
    if foreign:
        return foreign
    return pid, name


def _sidecar_user_app() -> int:
    """壳追踪的最近用户非 haochen app pid（HAOCHEN_HOME/last-user-app.txt）。"""
    try:
        home = Path(os.environ.get("HAOCHEN_HOME", ""))
        if home:
            f = home / "last-user-app.txt"
            if f.exists():
                return int(f.read_text().strip())
    except (ValueError, OSError):
        pass
    return 0


def _has_screen_window(pid: int) -> bool:
    """该 pid 是否有屏幕上的普通窗口。"""
    wins = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, 0)
    for w in wins or []:
        if int(w.get("kCGWindowLayer", -1)) == 0 and int(w.get("kCGWindowOwnerPID", -1)) == pid:
            return True
    return False


def _hap() -> int | None:
    """hHaochen app 的 pid（壳注入）；读者自身 os.getpid() 不等于 app pid。"""
    try:
        return int(os.environ.get("HAOCHEN_APP_PID")) if os.environ.get("HAOCHEN_APP_PID") else None
    except ValueError:
        return None


def _try_wake_chromium(ax_app) -> None:
    """唤醒 Chromium 系 App 的无障碍树（反复查询触发其 DOM 开放）。"""
    for attr in ("AXEnhancedUserInterface", "AXManualAccessibility"):
        try:
            AXUIElementSetAttributeValue(ax_app, attr, True)
        except Exception:
            pass


def _has_webarea(el, depth: int = 0, max_depth: int = 25) -> bool:
    if _copy(el, "AXRole") == "AXWebArea":
        return True
    if depth >= max_depth:
        return False
    return any(_has_webarea(c, depth + 1, max_depth) for c in (_copy(el, "AXChildren") or []))


def read_window(pid: int, app_name: str, allow_wait: bool = True) -> WindowContent:
    """读取指定 App 当前聚焦窗口一屏的内容。"""
    ax_app = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(ax_app, AX_TIMEOUT)
    _try_wake_chromium(ax_app)
    win = _copy(ax_app, "AXFocusedWindow")
    if win is None:
        wins = _copy(ax_app, "AXWindows")
        if wins:
            win = wins[0]
    if win is None:
        raise AXError(f"{app_name} 没有可读的窗口")
    title = _norm(_copy(win, "AXTitle"))
    blocks: list[Block] = []
    _walk(win, 0, blocks)
    if allow_wait and len(blocks) < 20 and not _has_webarea(win):
        for _ in range(12):
            time.sleep(0.5)
            _try_wake_chromium(ax_app)
            blocks = []
            _walk(win, 0, blocks)
            if len(blocks) >= 20 or _has_webarea(win):
                break
    bounds = (*_geom(win), *_size(win))
    return WindowContent(app_name=app_name, window_title=title, blocks=_dedup_sort(blocks),
                         window_bounds=bounds if bounds[2] > 0 and bounds[3] > 0 else None)


def _window_center(pid: int) -> tuple[float, float] | None:
    ax_app = AXUIElementCreateApplication(pid)
    win = _copy(ax_app, "AXFocusedWindow")
    if win is None:
        return None
    pos, size = _copy(win, "AXPosition"), _copy(win, "AXSize")
    try:
        return float(pos.x()) + float(size.width()) / 2, float(pos.y()) + float(size.height()) / 2
    except Exception:
        return None


def scroll_once(pid: int, lines: int = 30) -> None:
    """向目标窗口滚一屏：光标临时移到窗口中心，滚完移回原位。"""
    center = _window_center(pid)
    if center is None:
        return
    cur = CGEventGetLocation(CGEventCreate(None))
    CGWarpMouseCursorPosition(center)
    time.sleep(0.05)
    ev = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 1, -lines)
    CGEventPost(kCGHIDEventTap, ev)
    time.sleep(0.05)
    CGWarpMouseCursorPosition((cur.x(), cur.y()))


_TERMINALS = ("iterm", "terminal", "kitty", "alacritty", "warp", "wezterm", "hyper")


def read_full(max_scrolls: int = 20, stagnant_rounds: int = 3,
              on_progress=None, pid: int | None = None,
              app_name: str | None = None, time_budget: float = 60.0) -> WindowContent:
    """读前台窗口，自动向下滚动直到内容不再增长或到底（带总时长兜底）。"""
    if pid is None:
        pid, app_name = target_pid()
    # 终端类 App scrollback 近乎无限：只读当前视口附近
    if app_name and app_name.lower().replace("2", "").strip() in _TERMINALS:
        max_scrolls = min(max_scrolls, 3)
    deadline = time.monotonic() + time_budget
    first = read_window(pid, app_name)
    all_blocks: list[Block] = list(first.blocks)
    seen = {(b.kind, b.text, b.url) for b in all_blocks}
    stagnant = 0

    for i in range(max_scrolls):
        if time.monotonic() > deadline:
            break
        scroll_once(pid)
        time.sleep(0.4)
        snap = read_window(pid, app_name, allow_wait=False)
        if (snap.window_title != first.window_title or snap.window_bounds != first.window_bounds):
            break  # Never mix text from a newly focused window into the first one.
        new = [b for b in snap.blocks if (b.kind, b.text, b.url) not in seen]
        if new:
            stagnant = 0
            for b in new:
                seen.add((b.kind, b.text, b.url))
                all_blocks.append(b)
        else:
            stagnant += 1
        if on_progress:
            on_progress(i + 1, len(all_blocks))
        if stagnant >= stagnant_rounds:
            break

    result = WindowContent(app_name=first.app_name, window_title=first.window_title,
                           window_bounds=first.window_bounds)
    result.blocks = _dedup_sort(all_blocks)
    return result


def _validate_image(data: bytes) -> str | None:
    """校验图片字节可完整解码，返回以实际内容为准的 mime；损坏/不支持返回 None。

    v0.1.9：真机出现过下载到截断 PNG（有头无 IDAT/IEND），原样上送导致
    视觉端点 400「unsupported image」整轮失败。这里用 PIL 实际解码把关，
    mime 以内容为准（不信任响应头）。仅放行视觉端点通用格式。
    """
    if len(data) < 8:
        return None
    try:
        import io

        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
            fmt = (im.format or "").upper()
    except Exception:
        return None
    return {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif",
            "WEBP": "image/webp"}.get(fmt)


MAX_REMOTE_IMAGE_BYTES = 4 * 1024 * 1024
_ALLOWED_REMOTE_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _validate_remote_image_url(url: str) -> str:
    """Allow public HTTPS image URLs only; reject local and ambiguous destinations."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("remote images require an HTTPS URL")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("credentials and fragments are not allowed in remote image URLs")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid remote image port") from exc
    if port not in (None, 443):
        raise ValueError("remote images must use HTTPS port 443")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("local hostnames are not allowed")
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("remote image hostname could not be resolved") from exc
    if not addresses:
        raise ValueError("remote image hostname has no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise ValueError(f"remote image resolved to a non-public address: {ip}")
    return url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Apply the same network boundary to every redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = _validate_remote_image_url(urljoin(req.full_url if req is not None else "", newurl))
        return super().redirect_request(req, fp, code, msg, headers, target)


def _secure_urlopen(url: str):
    validated = _validate_remote_image_url(url)
    request = urllib.request.Request(validated, headers={"User-Agent": "haochen-reader/2.0"})
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    return opener.open(request, timeout=IMG_TIMEOUT)


def _download_as_data_url(url: str) -> str | None:
    # Embedded original image bytes need no network and must not be re-encoded.
    if url.startswith("data:"):
        try:
            header, encoded = url.split(",", 1)
            if not header.endswith(";base64") or len(encoded) > (MAX_REMOTE_IMAGE_BYTES * 4 // 3 + 4):
                return None
            mime = header[5:-7]
            if mime not in _ALLOWED_REMOTE_IMAGE_MIMES:
                return None
            raw = base64.b64decode(encoded, validate=True)
            return url if len(raw) <= MAX_REMOTE_IMAGE_BYTES and _validate_image(raw) == mime else None
        except (ValueError, TypeError):
            return None
    try:
        with _secure_urlopen(url) as resp:
            content_type = resp.headers.get_content_type().lower()
            if content_type not in _ALLOWED_REMOTE_IMAGE_MIMES:
                return None
            declared_size = resp.headers.get("Content-Length")
            if declared_size is not None:
                try:
                    declared_bytes = int(declared_size)
                    if declared_bytes < 0 or declared_bytes > MAX_REMOTE_IMAGE_BYTES:
                        return None
                except ValueError:
                    return None
            data = resp.read(MAX_REMOTE_IMAGE_BYTES + 1)
            if len(data) > MAX_REMOTE_IMAGE_BYTES:
                return None
            mime = _validate_image(data)
            if not mime or mime != content_type:
                return None
            return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except (OSError, ValueError, urllib.error.URLError):
        return None


def prepare_images(content: WindowContent, max_images: int = MAX_IMAGES) -> dict[int, str | None]:
    """并发下载窗口内容中的图片，返回 {block_index: data_url 或 None}。

    按可见尺寸/面积**从大到小**排序（大而显眼的人物图优先下载，让识人拿到最相关原图）。
    """
    img_idx = [(i, content.blocks[i].area) for i, b in enumerate(content.blocks)
               if b.kind == "image" and b.url]
    img_idx.sort(key=lambda x: x[1], reverse=True)   # 大的优先
    img_idx = [i for i, _ in img_idx[:max_images]]
    if not img_idx:
        return {}
    # Deduplicate only within THIS capture; no stale cross-turn image cache.
    # Retain the same chosen blocks, original bytes, size limits and concurrency.
    urls = list(dict.fromkeys(content.blocks[i].url for i in img_idx))
    with ThreadPoolExecutor(max_workers=8) as pool:
        downloaded = dict(zip(urls, pool.map(_download_as_data_url, urls), strict=True))
    return {i: downloaded[content.blocks[i].url] for i in img_idx}


class BoundTarget:
    """Metadata-only binding before consent; retain actual AX objects, not just PID."""
    def __init__(self, target):
        self.target = target
        self.window = matching_ax_window(target["pid"], target["window_id"])
        self.page = None
        self.source = None
        uri = target.get("document_uri", "")
        if uri.startswith("file:"):
            try:
                self.source = DocumentSource(uri)
            except (ValueError, OSError):
                pass
        if self.window is not None and self.matches_window():
            self.page = web_area(self.window)
            if self.page is not None:
                self.page_uri = str(_copy(self.page, "AXURL") or "")
        else:
            self.window = None
        if self.window is None and self.source is None:
            code = getattr(self, "mismatch_code", "window_unresolved")
            message = ("页面身份校验未通过；未读取正文。这不代表窗口已关闭，请重新确认目标。"
                       if code == "page_identity_mismatch" else
                       "暂时无法匹配目标窗口；未读取正文，不能据此判断窗口已关闭。")
            raise TargetError(code, message)

    def matches_window(self):
        # Compatibility for old targets: strict fingerprint. New targets compare
        # explicit metadata, distinguishing a missing URL from a changed URL.
        if "document_uri" not in self.target:
            return bool(self.target.get("fingerprint")) and fingerprint(self.window) == self.target["fingerprint"]
        expected = self.target["document_uri"]
        current = document_id(self.window)
        if expected:
            if expected != current:
                self.mismatch_code = "page_identity_mismatch"
                # Hash identity diagnostics: no page URLs, titles, body or keys in logs.
                print(json.dumps({"event": "identity_mismatch", "pid": self.target["pid"],
                                  "window_id": self.target["window_id"],
                                  "expected": hashlib.sha256(expected.encode()).hexdigest()[:12],
                                  "actual": hashlib.sha256(current.encode()).hexdigest()[:12]}), file=sys.stderr)
            return expected == current
        return str(_copy(self.window, "AXTitle") or "")[:180] == self.target.get("title", "")

    def readable_root(self):
        if self.page is not None:
            if (_copy(self.page, "AXRole") == "AXWebArea"
                    and str(_copy(self.page, "AXURL") or "") == self.page_uri):
                return self.page
            return None  # Never substitute the new tab's AXWebArea.
        if self.window is not None and self.matches_window():
            return self.window
        return None

    def file_snapshot(self):
        if self.source is None:
            raise AXError("原页面已不再提供可访问内容，且没有可读取的文档来源；未改读当前页面。")
        kind, value = self.source.read()
        block = Block(kind, text=value) if kind == "text" else Block(kind, url=value, alt=self.source.path.name)
        content = WindowContent(app_name=self.target.get("app", ""), window_title=self.target.get("title", ""),
                                blocks=[block], scope="已保存的本地文档版本；不包含未保存修改，并非当时屏幕快照")
        return content, None, kind == "image"


def read_bound_snapshot(target, binding=None):
    """Capture text and optional pixels together, before slow original-image downloads."""
    pid, wid = target.get("pid"), target.get("window_id")
    if not isinstance(pid, int) or not isinstance(wid, int) or not target.get("fingerprint"):
        raise AXError("尚未确认窗口身份。首次系统授权后，请回到目标页面重新提问。")
    binding = binding or BoundTarget(target)
    if binding.source is not None and binding.source.path.suffix.lower() in IMAGE_SUFFIXES:
        return binding.file_snapshot()  # Exact original bytes, not a downscaled preview screenshot.
    win = binding.readable_root()
    if win is None:
        return binding.file_snapshot()
    blocks = []
    _walk(win, 0, blocks)
    if len(blocks) < 20 and not _has_webarea(win):
        # Give an on-demand accessibility tree a bounded chance to populate.
        # All iterations stay on the already-bound AX window, never the foreground.
        for _ in range(4):
            if binding.readable_root() is None:
                raise AXError("页面已变化，请重新确认阅读目标。")
            time.sleep(0.1)
            blocks = []
            _walk(win, 0, blocks)
            if len(blocks) >= 20 or _has_webarea(win):
                break
    content = WindowContent(app_name=str(target.get("app", "")),
                            window_title=str(target.get("title", "")),
                            blocks=_dedup_sort(blocks), window_bounds=(*_geom(win), *_size(win)))
    needs_visual = any(b.kind == "image" for b in blocks) or sum(len(b.text) for b in blocks) < 80
    # A retained background page can be read, but its host window may show a new
    # tab: never attach that new tab's screenshot to the old page's text.
    same_visible_page = binding.matches_window()
    screenshot = (capture_window_image(pid, content.window_title, content.window_bounds, window_id=wid)
                  if needs_visual and same_visible_page else None)
    # A tab can change without changing its title/URL; check the extracted body too.
    after = []
    _walk(win, 0, after)
    def signature(values):
        return [(b.kind, b.text, b.url, b.alt) for b in _dedup_sort(values)]
    if (binding.readable_root() is None or signature(blocks) != signature(after)
            or (screenshot is not None and not binding.matches_window())):
        raise AXError("采集期间页面内容发生变化，本次内容已丢弃。请在页面稳定后重新阅读。")
    if not blocks and binding.source is not None:
        return binding.file_snapshot()
    return content, screenshot, needs_visual


# ── 视觉增强：截取目标窗口图像（P7，需屏幕录制权限）─────────────

MAX_IMAGE_BYTES = 4 * 1024 * 1024  # v0.1.9：单图超 4MB 先压缩再发（视觉端点常见上限 5MB）
MAX_IMAGE_SIDE = 2000              # 等比压缩目标长边


def _shrink_png_data_url(data_url: str | None) -> str | None:
    """截图防御（v0.1.9）：>4MB 的 PNG 等比压缩到长边 ≤2000px 重编码；
    仍超则逐半降档直至 ≤4MB。压缩失败返回 None（坏图宁缺毋滥——坏图会让
    视觉端点整请求 400）。"""
    if not data_url:
        return data_url
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        return data_url
    try:
        raw = base64.b64decode(data_url[len(prefix):])
    except Exception:
        return None
    if len(raw) <= MAX_IMAGE_BYTES:
        return data_url
    try:
        import io

        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im.load()
        scale = MAX_IMAGE_SIDE / max(im.size)
        if scale < 1:
            im = im.resize((max(1, round(im.size[0] * scale)),
                            max(1, round(im.size[1] * scale))), Image.LANCZOS)
        while True:
            buf = io.BytesIO()
            im.save(buf, "PNG")
            out = buf.getvalue()
            if len(out) <= MAX_IMAGE_BYTES or max(im.size) <= 800:
                break
            im = im.resize((max(1, im.size[0] // 2), max(1, im.size[1] // 2)),
                           Image.LANCZOS)
        return prefix + base64.b64encode(out).decode()
    except Exception:
        return None


def screen_capture_granted() -> bool:
    """是否已获屏幕录制权限（视觉读取用）。"""
    try:
        return bool(CGPreflightScreenCaptureAccess())
    except Exception:
        return False


def _cgimage_to_png_data_url(img) -> str | None:
    """CGImage → PNG base64 data URL。"""
    try:
        cfdata = CFDataCreateMutable(None, 0)
        dest = CGImageDestinationCreateWithData(cfdata, "public.png", 1, None)
        if dest is None:
            return None
        CGImageDestinationAddImage(dest, img, None)
        CGImageDestinationFinalize(dest)
        n = CFDataGetLength(cfdata)
        if n <= 0:
            return None
        buf = ctypes.create_string_buffer(n)
        CFDataGetBytes(cfdata, CFRangeMake(0, n), buf)
        import base64 as _b64
        return "data:image/png;base64," + _b64.b64encode(buf.raw[:n]).decode()
    except Exception:
        return None


def select_capture_window(windows, pid: int, title: str = "", bounds=None):
    """Match the same AX window; never replace it with a larger unrelated one."""
    candidates = [w for w in windows or [] if int(w.get("kCGWindowLayer", -1)) == 0
                  and int(w.get("kCGWindowOwnerPID", -1)) == pid]
    if bounds:
        for window in candidates:
            rect = window.get("kCGWindowBounds", {})
            if all(abs(float(rect.get(name, -99999)) - value) <= 4
                   for name, value in zip(("X", "Y", "Width", "Height"), bounds, strict=True)):
                return window
        return None  # Target moved/closed while images were fetched: do not substitute.
    if title:
        matches = [w for w in candidates if _norm(w.get("kCGWindowName")) == title]
        return matches[0] if len(matches) == 1 else None
    return candidates[0] if len(candidates) == 1 else None


def capture_window_image(pid: int, title: str = "", bounds=None, window_id=None) -> str | None:
    """截取目标窗口图像，返回 PNG data URL；无权限/失败返回 None。"""
    if not screen_capture_granted():
        return None  # 需要屏幕录制权限
    wins = CGWindowListCopyWindowInfo(
        kCGWindowListOptionAll | kCGWindowListExcludeDesktopElements, 0)
    best = (next((w for w in wins or [] if int(w.get("kCGWindowNumber", -1)) == window_id
                  and int(w.get("kCGWindowOwnerPID", -1)) == pid), None)
            if window_id is not None else select_capture_window(wins, pid, title, bounds))
    if best is None:
        return None
    wid = int(best["kCGWindowNumber"])
    img = CGWindowListCreateImage(CGRectNull, kCGWindowListOptionIncludingWindow, wid,
                                  kCGWindowImageBoundsIgnoreFraming)
    if img is None:
        return None
    return _shrink_png_data_url(_cgimage_to_png_data_url(img))


def serialize_snapshot(content, bound_shot, needs_visual, pid, *, bound=True):
    """Finish all original-image fetches before declaring the reading complete."""
    n_text = sum(1 for b in content.blocks if b.kind == "text")
    n_img = sum(1 for b in content.blocks if b.kind == "image")
    n_img_url = sum(1 for b in content.blocks if b.kind == "image" and b.url)
    data_urls = prepare_images(content)
    blocks = []
    for i, b in enumerate(content.blocks):
        if b.kind == "text":
            blocks.append({"kind": "text", "text": b.text, "role": b.role, "url": b.url})
        else:
            blk = {"kind": "image", "url": b.url, "alt": b.alt,
                   "area": b.area, "w": b.w, "h": b.h}
            if data_urls.get(i):
                blk["data_url"] = data_urls[i]
            blocks.append(blk)
    n_originals = sum(1 for b in data_urls.values() if b)
    if bound:
        screenshot = bound_shot if n_originals < n_img or n_img == 0 else None
        need_sr = needs_visual and screenshot is None and n_originals < max(1, n_img) and not screen_capture_granted()
    elif n_originals > 0:
        screenshot, need_sr = None, False
    else:
        screenshot = capture_window_image(pid, content.window_title, content.window_bounds)
        need_sr = screenshot is None and not screen_capture_granted()
    return {
        "app": content.app_name, "window_title": content.window_title,
        "stats": {"text_blocks": n_text, "images": n_img, "images_with_url": n_img_url},
        "screenshot": screenshot, "need_screen_recording": need_sr,
        "scope": content.scope if bound else "窗口读取",
        "images_original": n_originals, "images_unavailable": max(0, n_img - n_originals), "blocks": blocks,
    }


def serve():
    """Private stdin/stdout worker owned by one engine; never listens on a socket.

    Startup imports only. bind reads metadata only; read requires an explicit
    command matching that binding. EOF/engine exit releases all native objects.
    Cancellation during capture terminates this process from its owner.
    """
    def emit(request, event, **data):
        print(json.dumps({"id": request, "event": event, **data}, ensure_ascii=False), flush=True)
    emit(None, "ready")
    binding = None
    binding_id = None
    bound_at = 0
    for line in sys.stdin:
        request = None
        try:
            command = json.loads(line)
            request = command["id"]
            op = command["op"]
            start = time.monotonic()
            if op == "bind":
                binding, binding_id = None, None
                if not check_accessibility(prompt=False):
                    raise TargetError("permission_denied", "请在系统设置授予 haochen 辅助功能权限后重试。")
                binding = BoundTarget(command["target"])
                binding_id, bound_at = request, time.monotonic()
                emit(request, "bound", elapsed_ms=round((bound_at - start) * 1000))
            elif op == "read":
                if binding is None or binding_id != request or time.monotonic() - bound_at > 125:
                    raise TargetError("binding_expired", "阅读确认已失效；未读取其他窗口，请重新确认。")
                content, shot, visual = read_bound_snapshot(binding.target, binding)
                emit(request, "snapshot", elapsed_ms=round((time.monotonic() - start) * 1000))
                payload = serialize_snapshot(content, shot, visual, binding.target["pid"])
                emit(request, "result", data=payload, elapsed_ms=round((time.monotonic() - start) * 1000))
                binding, binding_id = None, None
            elif op == "cancel":
                if binding_id == request:
                    binding, binding_id = None, None
                emit(request, "cancelled")
            else:
                raise ValueError("unknown reader command")
        except (AXError, ValueError, KeyError, TypeError, OSError) as error:
            binding, binding_id = None, None
            emit(request, "error", code=getattr(error, "code", "capture_failed"), message=str(error))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="haochen-reader")
    ap.add_argument("--json", action="store_true", help="输出 JSON（必传，保持契约一致）")
    ap.add_argument("--check", action="store_true", help="仅检测辅助功能权限（退出码 0=已授权, 2=未授权）")
    ap.add_argument("--pid", type=int, default=None, help="锁定读取指定进程窗口")
    ap.add_argument("--target-json", help="本轮固定窗口身份，仅由应用壳提供")
    ap.add_argument("--bind", action="store_true", help="先绑定对象，等待 stdin 的 read 授权再采集")
    ap.add_argument("--serve", action="store_true", help="复用进程；仅明确 read 命令后读取正文")
    ap.add_argument("--no-scroll", action="store_true", help="只读当前一屏")
    ap.add_argument("--max-scrolls", type=int, default=20, help="最多滚动屏数（默认 20）")
    args = ap.parse_args()
    if args.serve:
        return serve()

    if args.check:
        granted = check_accessibility(prompt=False)
        print(json.dumps({"accessibility": granted,
                          "screen_recording": screen_capture_granted()}))
        return 0 if granted else 2

    if not check_accessibility(prompt=False):
        print("未获得「辅助功能」权限。请到 系统设置 → 隐私与安全性 → 辅助功能 中"
              "勾选 haochen，然后重试。", file=sys.stderr)
        return 2

    def progress(rounds, n):
        print(f"\r滚动第 {rounds} 屏，累计 {n} 个内容块...", end="", flush=True, file=sys.stderr)

    try:
        bound_shot = None
        needs_visual = False
        if args.target_json:
            target = json.loads(args.target_json)
            pid = target["pid"]
            binding = BoundTarget(target)
            if args.bind:
                print(json.dumps({"event": "bound"}), flush=True)
                # A dead parent, refusal or abandoned dialog must never leave a
                # worker reading or holding objects indefinitely.
                if not select.select([sys.stdin], [], [], 125)[0] or sys.stdin.readline().strip() != "read":
                    return 0
            content, bound_shot, needs_visual = read_bound_snapshot(target, binding)
        elif args.pid is not None:
            pid, app_name = args.pid, app_name_for_pid(args.pid)
            content = (read_window(pid, app_name) if args.no_scroll
                       else read_full(max_scrolls=args.max_scrolls,
                                      on_progress=progress, pid=pid, app_name=app_name))
        else:
            pid, app_name = target_pid()
            content = (read_window(pid, app_name) if args.no_scroll
                       else read_full(max_scrolls=args.max_scrolls,
                                      on_progress=progress, pid=pid, app_name=app_name))
        if not args.no_scroll:
            print(file=sys.stderr)
    except (AXError, ValueError, KeyError, TypeError, OSError) as e:
        print(f"读取失败: {e}", file=sys.stderr)
        return 1

    print(json.dumps(serialize_snapshot(content, bound_shot, needs_visual, pid, bound=bool(args.target_json)),
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
