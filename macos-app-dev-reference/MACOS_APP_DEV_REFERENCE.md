# macOS App 开发规范参考（针对 haochen 项目）

> 用途：haochen 要交付独立 macOS App（.dmg）。本文件整理打包/签名/权限/公证等关键规范，作为项目开发依据。来源：Apple 官方文档要点 + Python/PyQt 打包实践。

## 0. 本项目技术栈（打包视角）

- 引擎：pi 核心用 **Bun 打包成单文件可执行**（E1），作为 App 内嵌二进制。
- 前端/桌宠：**Python + PyQt6**（复用现有桌宠）。
- 交付：`.app` bundle → `.dmg`。
- 打包工具选项：**PyInstaller**（成熟、支持 PyQt）+ 手动组 .app / `hdiutil` 或 `create-dmg`。py2app 也可，但 PyInstaller 对 Qt 更稳。

## 1. macOS App bundle 结构（.app）

```
haochen.app/
├── Contents/
│   ├── Info.plist          # 应用元信息（CFBundleIdentifier、CFBundleName、版本、最低系统版本）
│   ├── MacOS/
│   │   └── haochen         # 主可执行（PyInstaller 生成，内含 Python 运行时 + PyQt + 业务代码）
│   ├── Resources/
│   │   └── haochen-engine  # 引擎单文件可执行（Bun 打包的 pi 核心），App 启动时调用
│   │       ├── ...（引擎运行时资源）
│   ├── Frameworks/         # PyInstaller 把 Qt 框架等放这（onefile 则内嵌）
│   └── PkgInfo             # "APPL????"（可选）
```

- `Info.plist` 关键键：`CFBundleIdentifier`（如 `com.haochen.app`）、`CFBundleName`、`CFBundleVersion`、`LSMinimumSystemVersion`、`NSHighResolutionCapable=true`、`NSPrincipalClass=NSApplication`（仅 Cocoa 应用）。
- **onefile vs onedir**：PyInstaller `--onedir` 更利于调试/签名；`--onefile` 易于分发但启动慢、签名略麻烦。建议 onedir 打包后组 .app。

## 2. 签名（Code Signing）

- 本地开发：`codesign --force --deep --sign - <app路径>`（`-` = ad-hoc 签名）。
- 分发建议：Developer ID Application 证书签名（`codesign --sign "Developer ID Application: xxx"`），配合公证。
- 注意：**改了二进制或资源后需重新签名**；`--deep` 对嵌套二进制（引擎可执行）也签名。
- 引擎可执行（Bun 产物）也需签名（可先 codesign 再嵌进 .app，或用 `--deep`）。

## 3. 公证（Notarization，分发必备）

- 用 Apple 公证服务对 App 做 `notarytool submit`（需 Apple ID / app-specific password，或 API key）。
  ```
  xcrun notarytool submit haochen.app.zip --keychain-profile haochen-profile --wait
  ```
- 成功后 `xcrun stapler staple haochen.app`，把票据贴进 App。
- Gatekeeper：未公证/未签名的 App，用户首次打开会被拦（"无法验证开发者"）。公证 + 签名后正常打开；或用 `xattr -cr` 临时绕开。

## 4. App Sandbox（关键权衡）

- 若开启沙盒（`com.apple.security.app-sandbox=true`），App 默认只能访问自身容器，且需要 entitlement 才能用网络/硬件/系统服务。
- **本项目的读屏依赖「辅助功能（AX）」和「屏幕录制」权限**，这些在沙盒内更受限、且需要用户在系统设置手动授权。
- **决策：haochen 做非沙盒应用**（`com.apple.security.app-sandbox` 不设），因为要读屏/访问用户文件/跑命令。代价是 App Store 分发受限；直接 .dmg 分发不受影响。若走 App Store 则需重审沙盒/权限方案。

## 5. 系统权限（本项目必须）

| 权限 | 用途 | Info.plist 键 / 触发 |
|------|------|----------------------|
| 辅助功能 (Accessibility) | 读取窗口内容（AX API，读屏核心） | 无 Info.plist 键；首次访问 `AXIsProcessTrusted` 时提醒用户到 `系统设置→隐私与安全性→辅助功能` 勾选应用 |
| 屏幕录制 | 若走截图 OCR 兜底 | `NSScreenCaptureUsageDescription`（macOS 10.15+ 需用户授权屏幕录制） |
| 本地网络（可选） | 访问模型 API 不涉及本地网络；沙盒网络 entitlement 需 `com.apple.security.network.client` | 非沙盒无此项 |

## 6. 打包 dmg

- `hdiutil create -volname "haochen" -srcfolder haochen.app -ov -format UDZO haochen.dmg`
- 或社区工具 `create-dmg`（更美观、可加背景/链接）。
- 分发前：签名 → 公证 → stapler → 打 dmg（或先打 zip 公证，再 dmg）。

## 7. 关键命令速查

```bash
# PyInstaller 打 onedir（含 PyQt + 业务）
pyinstaller --noconfirm --onedir --windowed --name haochen \
  --add-binary "<engine可执行>;Resources" \
  haochen_main.py

# 组成 .app（若 PyInstaller 只出 onedir，手动包一层）
# 或直接用 py2app 直接产 .app：python setup.py py2app

# ad-hoc 签名
codesign --force --deep --sign - haochen.app

# 公证
ditto -c -k --keepParent haochen.app haochen.zip
xcrun notarytool submit haochen.zip --keychain-profile haochen-profile --wait
xcrun stapler staple haochen.app

# 打 dmg
hdiutil create -volname "haochen" -srcfolder haochen.app -ov -format UDZO haochen.dmg
```

## 8. 风险提示

1. **PyQt + PyInstaller**：需确保 libc++/Qt 插件/平台插件打包齐全；用 `--hidden-import` 补 PyQt6 相关模块；测试 `.app` 能在干净机器跑。
2. **引擎可执行嵌入**：Bun 产物是独立二进制，加入 Resources 后需一并签名，且 App 内启动路径要写对（`sys.argv`/`__file__` 定位 resource）。
3. **权限授权体验**：首次运行需引导用户授权辅助功能/屏幕录制，否则读屏报错（沿用现有 AX 检查+提示逻辑）。
4. **公证 + 非沙盒**：本地分发（非 App Store）OK；公证配置（Apple ID/密钥）需准备。
