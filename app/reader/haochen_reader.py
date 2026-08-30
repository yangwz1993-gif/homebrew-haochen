#!/usr/bin/env python3
"""haochen 读屏执行体（独立单文件，PyInstaller --onefile 冻结为 haochen-reader）。

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
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import AppKit
from ApplicationServices import (
    AXIsProcessTrustedWithOptions,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementSetAttributeValue,
    AXUIElementSetMessagingTimeout,
)
from Quartz import (
    CGEventCreate,
    CGEventCreateScrollWheelEvent,
    CGEventGetLocation,
    CGEventPost,
    CGImageDestinationAddImage,
    CGImageDestinationCreateWithData,
    CGImageDestinationFinalize,
    CGPreflightScreenCaptureAccess,
    CGWindowListCopyWindowInfo,
    CGWindowListCreateImage,
    CGWarpMouseCursorPosition,
    CGRectNull,
    kCGHIDEventTap,
    kCGScrollEventUnitLine,
    kCGWindowImageBoundsIgnoreFraming,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionIncludingWindow,
    kCGWindowListOptionOnScreenOnly,
)
from CoreFoundation import CFDataCreateMutable, CFDataGetBytes, CFDataGetLength, CFRangeMake

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

    @property
    def area(self) -> float:
        return self.w * self.h if self.w and self.h else 0.0


@dataclass
class WindowContent:
    app_name: str = ""
    window_title: str = ""
    blocks: list[Block] = field(default_factory=list)


class AXError(RuntimeError):
    pass


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
        return 0.0, 0.0


def _size(el) -> tuple[float, float]:
    """读取元素可见尺寸（AXSize）。"""
    size = _copy(el, "AXSize")
    try:
        return float(size.width()), float(size.height())
    except Exception:
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

    if role in TEXT_VALUE_ROLES:
        text = _norm(_copy(el, "AXValue")) or _norm(_copy(el, "AXTitle"))
        if text:
            x, y = _geom(el)
            out.append(Block(kind="text", text=text, x=x, y=y))
        return  # 文本节点不必再深入
    if role in TITLE_ROLES:
        text = _norm(_copy(el, "AXTitle")) or _norm(_copy(el, "AXDescription"))
        if text:
            x, y = _geom(el)
            out.append(Block(kind="text", text=text, x=x, y=y))
        # 链接/按钮里可能包图片，继续走子节点
    elif role in IMAGE_ROLES:
        url = _copy(el, "AXURL")
        url = str(url) if url else None
        alt = _norm(_copy(el, "AXDescription")) or _norm(_copy(el, "AXTitle"))
        if url or alt:
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
    return WindowContent(app_name=app_name, window_title=title, blocks=_dedup_sort(blocks))


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

    result = WindowContent(app_name=first.app_name, window_title=first.window_title)
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


def _download_as_data_url(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "haochen-reader/1.0"})
        with urllib.request.urlopen(req, timeout=IMG_TIMEOUT) as resp:
            data = resp.read(8 * 1024 * 1024)
            mime = _validate_image(data)
            if not mime:
                return None
            return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return None


def prepare_images(content: WindowContent, max_images: int = MAX_IMAGES) -> dict[int, "str | None"]:
    """并发下载窗口内容中的图片，返回 {block_index: data_url 或 None}。

    按可见尺寸/面积**从大到小**排序（大而显眼的人物图优先下载，让识人拿到最相关原图）。
    """
    img_idx = [(i, content.blocks[i].area) for i, b in enumerate(content.blocks)
               if b.kind == "image" and b.url]
    img_idx.sort(key=lambda x: x[1], reverse=True)   # 大的优先
    img_idx = [i for i, _ in img_idx[:max_images]]
    if not img_idx:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda i: _download_as_data_url(content.blocks[i].url), img_idx)
    return dict(zip(img_idx, results))


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


def capture_window_image(pid: int) -> str | None:
    """截取目标窗口图像，返回 PNG data URL；无权限/失败返回 None。"""
    if not screen_capture_granted():
        return None  # 需要屏幕录制权限
    wins = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, 0)
    best = None
    best_area = 0.0
    # 优先：目标 pid 的最大普通窗口（layer 0）；若没有（如系统通知弹窗/宠物前台），
    # 回退到屏幕最大普通窗口（用户的实际窗口），避免截到通知/宠物自身。
    for w in wins or []:
        if int(w.get("kCGWindowLayer", -1)) != 0:
            continue
        b = w.get("kCGWindowBounds", {})
        area = float(b.get("Width", 0)) * float(b.get("Height", 0))
        if int(w.get("kCGWindowOwnerPID", -1)) == pid:
            if area > best_area:
                best_area = area
                best = w
    if best is None:
        for w in wins or []:
            if int(w.get("kCGWindowLayer", -1)) != 0:
                continue
            b = w.get("kCGWindowBounds", {})
            area = float(b.get("Width", 0)) * float(b.get("Height", 0))
            if area > best_area:
                best_area = area
                best = w
    if best is None:
        return None
    wid = int(best["kCGWindowNumber"])
    img = CGWindowListCreateImage(CGRectNull, kCGWindowListOptionIncludingWindow, wid,
                                  kCGWindowImageBoundsIgnoreFraming)
    if img is None:
        return None
    return _shrink_png_data_url(_cgimage_to_png_data_url(img))


def main() -> int:
    ap = argparse.ArgumentParser(prog="haochen-reader")
    ap.add_argument("--json", action="store_true", help="输出 JSON（必传，保持契约一致）")
    ap.add_argument("--check", action="store_true", help="仅检测辅助功能权限（退出码 0=已授权, 2=未授权）")
    ap.add_argument("--pid", type=int, default=None, help="锁定读取指定进程窗口")
    ap.add_argument("--no-scroll", action="store_true", help="只读当前一屏")
    ap.add_argument("--max-scrolls", type=int, default=20, help="最多滚动屏数（默认 20）")
    args = ap.parse_args()

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
        if args.pid is not None:
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
    except AXError as e:
        print(f"读取失败: {e}", file=sys.stderr)
        return 1

    n_text = sum(1 for b in content.blocks if b.kind == "text")
    n_img = sum(1 for b in content.blocks if b.kind == "image")
    n_img_url = sum(1 for b in content.blocks if b.kind == "image" and b.url)
    print(f"读到 {n_text} 段文本、{n_img} 张图片（其中 {n_img_url} 张带 URL）"
          f"，来自 [{content.app_name}] {content.window_title}", file=sys.stderr)

    data_urls = prepare_images(content)
    blocks = []
    for i, b in enumerate(content.blocks):
        if b.kind == "text":
            blocks.append({"kind": "text", "text": b.text})
        else:
            blk = {"kind": "image", "url": b.url, "alt": b.alt,
                   "area": b.area, "w": b.w, "h": b.h}
            if data_urls.get(i):
                blk["data_url"] = data_urls[i]
            blocks.append(blk)
    # 口图策略（P7 优化）：优先喂「图片原图 URL 下载」最准；
    # 截图仅在**无 URL 原图可下载**（动态图/canvas/图片型UI）时才截，作兜底。
    n_originals = sum(1 for b in data_urls.values() if b)
    if n_originals > 0:
        screenshot = None
        need_sr = False
    else:
        screenshot = capture_window_image(pid)
        need_sr = screenshot is None and not screen_capture_granted()
    print(json.dumps({
        "app": content.app_name,
        "window_title": content.window_title,
        "stats": {"text_blocks": n_text, "images": n_img, "images_with_url": n_img_url},
        "screenshot": screenshot,
        "need_screen_recording": need_sr,
        "blocks": blocks,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
