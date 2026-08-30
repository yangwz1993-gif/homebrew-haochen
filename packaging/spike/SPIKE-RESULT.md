# P1-B 打包探针结果（packaging spike）

日期：2026-08-25 ・ 平台：macOS 15.6.1 arm64 (MacBook Air) ・ 执行：Agent-B

## 总结论：✅ 通过

「PyInstaller + PyQt6 + 内嵌二进制 → .app → ad-hoc 签名 → 双击运行」全链路打通，
四个验证目标全部达成，无阻断性风险。

## 版本号

| 组件 | 版本 |
|---|---|
| Python | 3.12.13（uv standalone，装在 spike 目录内 `.python/`，未碰系统/全局） |
| PyInstaller | 6.22.2 |
| PyQt6 | 6.11.0（Qt 6.11.2，PyQt6-Qt6 wheel） |
| 构建产物 | `dist/SpikeProbe.app`，74 MB（onedir），Mach-O arm64 |

## 每步结果

| 步骤 | 结果 | 说明 |
|---|---|---|
| venv + 依赖安装 | ✅ | uv 一键，无编译 |
| PyInstaller `--onedir --windowed` 直出 .app | ✅ | 一次成功，无需手动组 bundle、无需 hidden-import |
| LSUIElement=true | ✅ | PlistBuddy 后注入；`lsappinfo` 显示 `type="UIElement"`，Dock/前台进程列表中无该 app |
| 内嵌假引擎于 `Contents/Resources/` | ✅ | spawn + 发一行收一行成功，标签显示 `OK 引擎回复: PONG (got: PING, pid: N, arch: arm64)` |
| ad-hoc 签名 | ✅ | `codesign --force --deep --sign -` + `--verify --deep --strict` 通过 |
| `open`（=Finder 双击）运行 | ✅ | 窗口正常弹出，进程稳定存活（实测 120s+） |

## 资源定位结论（__file__ vs sys._MEIPASS）

PyInstaller 6 onedir .app 的实际布局（本次实测）：

- `--add-data "fake-engine.sh:."` 的 data 文件**实体落在 `Contents/Resources/`**，
  `Contents/Frameworks/` 下只放一个同名**符号链接**指向它（`Frameworks` 即 `sys._MEIPASS`）。
- 因此两条路径都可用：`sys._MEIPASS/name`（经符号链接）与
  `dirname(sys.executable)/../Resources/name`（实体）。
- 探针采用后者（显式放 Resources，符合"引擎放 Resources"的需求），`app.py` 中
  `engine_path()` 用 `sys.frozen` 区分冻结/脚本两种形态，均已验证。
- 注意：`sys.executable` 在 frozen 下是 `Contents/MacOS/<AppName>`，`__file__` 在 frozen 下
  指向 `sys._MEIPASS` 内的临时路径语义，**不要**用 `__file__` 推 Resources。

## 踩坑记录

1. **系统 Python 3.9.6 太旧**：新版 PyQt6/PyInstaller 对 3.9 支持已边缘化。解法：uv standalone
   Python 3.12，用 `UV_PYTHON_INSTALL_DIR` 锁在 spike 目录内，零全局污染。
2. **LSUIElement 注入时机**：PyInstaller 生成的 Info.plist 无此键，需构建后
   `PlistBuddy Add :LSUIElement bool true`；**改 plist / Resources 后必须重签**，否则签名失效。
3. **签名顺序**：PyInstaller 自己会先签一遍（"Signing the BUNDLE"），任何 post-build 修改
   （拷贝引擎、改 plist）之后必须 `codesign --force --deep --sign -` 重签。
4. **验证脚本的小坑**：`grep -c` 无匹配时退出码 1，会掐断 `&&` 链；osascript 的前台进程列表
   只给名字不给 pid，多实例同 bundleID 时要用 `unix id of every process` 区分。
5. **观察过一次、未复现的现象**：最早三次启动的实例在约 50s 后以 rc=0 自行退出（当时并行做了
   多次截图/osascript/多实例操作）。之后 `open` 启动的实例稳定存活 120s+，终验两次均正常。
   判断为早期验证操作干扰（多实例同 bundleID），非打包缺陷；建议 M-F 阶段做一次
   "干净环境下双击后静置 10 分钟"的回归确认。
6. **SDK 版本提示**：PyInstaller 日志出现 `Rewriting the executable's macOS SDK version
   (26.2.0) to match the SDK version of the Python library (15.5.0)` —— 无害，是
   PyInstaller 6.x 对 Xcode CLT 与 Python 构建 SDK 差异的自动修正。

## Qt 插件 / hidden-import

- 本探针**未需要任何 hidden-import**；PyInstaller 的 PyQt6 hook 自动处理了
  `QtCore/QtGui/QtWidgets` 及 Qt 插件（`platforms/libqcocoa.dylib` 等已收进
  `Contents/Frameworks/PyQt6/Qt6/plugins/`，含 platforms/imageformats/styles/iconengines/generic）。
- frozen 与非冻结的窗口渲染一致，无 "could not load the Qt platform plugin" 类问题。

## 截图证据（verification/）

- `spike-unfrozen-window.png` — 源码直跑（非冻结）窗口 + PONG
- `spike-frozen-nolsuie-window.png` — 冻结版对照（无 LSUIElement）窗口 + PONG
- `spike-frozen-lsuie-window.png` — `open` 启动 LSUIElement 版窗口 + PONG（pid 24502）
- `spike-final-open.png` — 终验：`open` 启动重建后的 .app，窗口 + PONG（pid 25287）
- `spike-window.png` / `spike-window2.png` — 早期全屏截图（窗口被遮挡期，仅过程记录）

程序化验证记录：`lsappinfo` → `type="UIElement"`；osascript 前台进程 pid 列表中无该 app（count=0，
同 bundleID 无 LSUIElement 对照组 count=1，证明检测有效）。

## 对 M-F 正式打包的建议

1. **直出可行**：PyInstaller `--onedir --windowed` 直出 .app 即可，不需要手动组 bundle；
   建议固化一个 `build.sh`（本探针版本可直接演进）。
2. **引擎内嵌**：引擎可执行放 `Contents/Resources/`，代码侧用
   `dirname(sys.executable)/../Resources/<engine>` 定位；别忘了 `chmod +x` 和**改后重签**。
   真引擎若是 Mach-O 而非脚本，`codesign --deep` 会一并签，但要确认它本身的依赖（dylib）也进 bundle。
3. **Python 版本管理**：沿用 uv standalone Python（建议 3.12），构建机/CI 均可复现；
   不要依赖 macOS 系统 Python。
4. **hardened runtime / entitlements 预判**（分发阶段才需要，本探针 ad-hoc 已够）：
   - 公证（notarization）要求 hardened runtime：`codesign --options runtime`。
   - 最小 entitlements 预判：主进程通常不需要额外 entitlement；但**子进程 spawn 外部引擎 +
     stdio 管道**在 hardened runtime 下要留意——引擎若以独立可执行被 spawn，需
     `com.apple.security.inherit` 语义确认，必要时给 app 加
     `com.apple.security.cs.disable-library-validation`（加载第三方/未同台签名的 Qt dylib 时常见）。
   - ad-hoc 签名的 .app 在别的机器上会被 Gatekeeper 拦（"已损坏"），正式分发必须
     Developer ID 签名 + 公证；本机内测 ad-hoc 足够。
5. **体积**：onedir 74 MB 起步（Qt 全量 wheel）。M-F 可考虑 `--exclude-module` 裁掉
   QtPdf/QtSvg/QtNetwork 等未用模块，或评估 UPX（注意 UPX 与公证的兼容性需单独验证）。
6. **未复现的 50s 自退现象**：建议 M-F 在干净环境做一次长时间静置回归（见踩坑 5）。
