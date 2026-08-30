# macOS 应用授权（读屏幕/辅助功能）规范开发流程

> 目的：给 haochen 的权限授权（读屏「辅助功能」+ 截图看圖「屏幕录制」）一套**符合 macOS 规范的开发流程**，解决"点已授权仍检测不到 / 反复授权"的问题。
> 说明：`search-enhancement` 当前网络检索受限（google 空、bing 结果不贴题），本文基于 macOS TCC 规范 + 本机实机证据整理。

---

## 一、核心机制：TCC（Transparency, Consent, Control）

macOS 把「辅助功能 / 屏幕录制 / 等」权限统一由 **TCC** 管理，关键规则：

1. **按"责任进程"归因**：TCC 授权记录绑定的不是"进程名"，而是**code 的 designated requirement (DR)** + 责任进程。
2. **稳定签名 → 归因正确 + 授权持久**：用 **Apple Developer ID**（或自签**受信任**证书）稳定签名 → DR 固定 → 权限归到 app 本身 → **授权一次永久有效**。
3. **adhoc 签名（`--sign -`）的坑**：每次构建 `DR` 都变 → ①TCC 认为每次是"新 app"，需重新授权；②从终端/其它进程跑的子进程，TCC 归因到"责任进程"（可能是终端而不是 app）→ **检测不一致**。这是 haochen 反复授权/检测不到的老根因。

---

## 二、规范开发流程（正确做法）

### 1. 先保证**稳定签名**（前提，最重要）
- 用 Apple Developer ID，或本地自签**受信任**证书（stable identity），确保 app 有唯一稳定的 DR。
- **不要用 adhoc**（授权不持久 + 责任进程错）。
- 构建/重签用**同一稳定身份**（`codesign --sign <identity>`），DR 固定。

### 2. Info.plist 权限描述
- **屏幕录制**：`NSScreenCaptureUsageDescription`（macOS 10.15+ 及最新版都需要，说明为什么录屏/截图）。
- **辅助功能**：无 Info.plist 键（属于 TCC "允许控制你电脑" 类别，靠稳定签名归属）。

### 3. 检测 + 请求（用正确 API）
| 权限 | 检测 | 请求（触发系统弹窗） |
|------|------|---------------------|
| 辅助功能 | `AXIsProcessTrustedWithOptions({AXTrustedCheckOptionPrompt:false})` | 同 API `prompt:true` |
| 屏幕录制 | `CGPreflightScreenCaptureAccess()` | `CGRequestScreenCaptureAccess()` |

- **检测必须在 app 责任进程内调用**（不是从终端/子进程），否则读到的是错误责任进程的授权。

### 4. 授权引导 UI（规范）
```
检测未授权 → 引导卡片（说明 + 用途）
  ├─ 「去授权」→ 触发系统弹窗 或 打开系统设置对应面板
  │     x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility
  │     x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture
  └─ 「我已授权」→ 重新检测（AXIsProcessTrusted / CGPreflightScreenCaptureAccess）
      授权成功 → 关卡片；仍未授权 → 提示可能旧签名/需重启
```

### 5. 权限刷新
- TCC 授权后，**部分场景需重启 App** 才能让 App 重新读到授权（缓存）→ 规范做法：授权后**重新检测**（或提示重启）。

---

## 三、针对 haochen 现状的建议（修复方向）

1. **稳定签名必须是打包默认**（已让 kimi 用 `haochen Local Signing`，DR 固定）——这是根治反复授权。
2. **权限检测在 app 责任进程内**：`permissions.py` 的 `accessibility_granted` / `screen_recording_granted` 要在 **app 自身进程**调用（而非子进程/终端），并确认用正确 API + 选项。
3. **引导卡片规范**（已有）：去授权（跳系统设置正确面板）+ 我已授权（重新检测 + 提示重启）。
4. **排查"系统已授权(开关蓝)但 App 检测 False"**：多为 ①责任进程定位错（检测在子进程/终端跑）②TCC 记录绑定的是旧 DR（曾 adhoc 授权）/或不同路径副本 ③授权后未刷新。稳定签名 + app 内检测 + 授权后重测 即可对齐。

---

## 四、参考（Apple 官方 / TCC）
- `AXIsProcessTrustedWithOptions`（ApplicationServices）
- `CGPreflightScreenCaptureAccess` / `CGRequestScreenCaptureAccess`（CoreGraphics）
- Info.plist `NSScreenCaptureUsageDescription`
- `x-apple.systempreferences:...` 深链到系统设置权限面板
