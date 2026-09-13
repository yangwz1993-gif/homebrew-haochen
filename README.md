# haochen

haochen 是一款面向 macOS 的独立桌面 AI 伙伴：常驻桌面、按需读取当前窗口、调用本机工具，并用轻量气泡或完整工作台返回结果。

[![CI](https://github.com/yangwz1993-gif/homebrew-haochen/actions/workflows/ci.yml/badge.svg)](https://github.com/yangwz1993-gif/homebrew-haochen/actions/workflows/ci.yml)
[![milestone](https://img.shields.io/badge/milestone-0.3.4-3a7d5c)](https://github.com/yangwz1993-gif/homebrew-haochen/releases/tag/v0.3.4)
[![platform](https://img.shields.io/badge/platform-macOS-2b2b22)](#系统要求)

## 当前状态

- 稳定里程碑：`0.3.4`（所有者验收通过；历史记录为 472 项自动化测试、整体覆盖率 83%）。
- 默认 Homebrew 配方 `haochen`：远程 `main` 本次核对仍为 `0.3.3`，与 `0.3.4` 里程碑存在历史不同步；默认安装/升级不能据此承诺拿到 `0.3.4`。
- 当前测试候选：`0.5.0-beta.3`，已完成本机替换安装和部分真实连接验证，**不是全部功能验收通过的正式版**。
- 本次分发计划：GitHub 预发布 + 独立 `haochen@beta` Cask；默认 `haochen` 保持现状，不自动将稳定通道用户升级到 beta。发布授权已取得，以下 beta 链接和命令为预期入口，发布及下载验证完成前不视为已上线。

0.5 增加原生桌面总览、刘海/侧边入口、Otty Agent 动态、授权网页追踪、日历、文件条、自建事项和日报；haochen 仍是右下角的全局助手。接入要求和已知限制见下文；完整状态与后续优先级见 [下一位开发者交接](docs/handoff/v0.5.0-beta.3.md)。

沿用所有者选择的 legacy DMG 分发：稳定发布者自签名，Cask 使用已明确披露的 quarantine 处理。不是 Apple Developer ID 签名，也未通过 Apple 公证；直接下载 DMG 可能被 Gatekeeper 拦截。

## 安装

默认通道（当前配方仍是 `0.3.3`）：

```bash
brew tap yangwz1993-gif/haochen
brew install --cask haochen
open -a haochen
```

已通过 Homebrew 安装的用户：`brew update && brew upgrade --cask haochen`。

0.5 独立测试通道（预期安装方式，须先确认预发布资产与配方已实际上线）：

```bash
brew tap yangwz1993-gif/haochen
brew update
brew install --cask yangwz1993-gif/haochen/haochen@beta
open -a haochen
```

预期下载入口：[v0.5.0-beta.3 预发布](https://github.com/yangwz1993-gif/homebrew-haochen/releases/tag/v0.5.0-beta.3)。已安装 beta 后使用 `brew upgrade --cask yangwz1993-gif/haochen/haochen@beta`。

两个通道安装的是同名 `haochen.app`，不是可同时运行的两套应用。已有默认通道安装时，先备份应用数据，再移除旧 App/原 Cask 后切换通道；不要使用 `--zap` 清除数据。两版共享应用数据，降级兼容性尚未验收。

首次使用时，按向导确认 haochen 如何称呼你，并选择 DeepSeek 或填写自定义 OpenAI 兼容模型的 URL、模型 ID 和 API Key。Key 存储在 macOS Keychain；辅助功能和屏幕录制权限只在相关能力需要时申请。

## 0.5 beta 接入要求与已知限制

| 能力 | 当前事实与边界 |
| --- | --- |
| Otty Agent | 官方 CLI 可发现终端；隔离真实 Pi 的 `processing → idle` 和精确来源跳转已验证。但本机现有 2 个会话均为 `unknown`、无 session ID；Pi 默认位置集成文件存在但未上报，另一个 Agent 类型尚未识别。不能把“发现终端”当作“Agent 状态已接通”。约 3 秒快照仍可能漏掉轮询间短状态。 |
| Chrome | 已实测首次授权一次点击、真实正文、正文更新、表单排除、精确跳转及切页保护。需在 `chrome://extensions` 加载 `/Applications/haochen.app/Contents/Resources/browser-extension`，再由 App 完成本机桥接；逐站授权、逐页明确选择，不自动扫描全部标签。未上架扩展商店；当前只覆盖主框架 DOM 文字，图片、Canvas 等不在覆盖范围内。 |
| 微信 | 仅可选观察公开 Dock 未读数字，不读取聊天正文。真实测试返回 `kAXErrorNoValue`，当前不能取得标记；不等于未读为零，正向消息提醒尚未验收。 |
| 模型 | 支持预设与自定义兼容接口；本机钥匙串授权仍需用户本人完成，本轮不能记为模型已成功连接。 |
| 日历 / 多屏 | 有实现及隔离测试，用户日历授权与多屏实机体验尚未验收。 |
| 事项 / 日报 | 由用户添加事项和追踪渠道；不是全应用、全项目自动覆盖。工程测试不替代真实渠道刷新和用户内容验收。飞书、企业 Hi 暂缓。 |

Chrome 详细设置见 [扩展说明](browser-extension/README.md)。Otty 集成先诊断再操作：Pi/OMP 仅在用户确认后新增官方集成文件，不覆盖已有文件或重启现有 Agent；Codex/Claude 按 Otty 官方设置配置。**不要反复安装已存在的 Pi 文件来掩盖未上报问题。**

beta.3 工程门禁：Python **795 passed / 1 opt-in skipped**、Node **42/42**、实际隔离 WK **76/76**、冻结包自检 **15/15**。Otty 实装生命周期、原生入口/窗口层级验证来自 beta.2；beta.3 专项实装复测验证 Chrome 首次授权修复，未重复全套 Otty 实装测试。详见 [连接验收记录](docs/reviews/v0.5.0-beta.3-connections.md) 与 [本地交付记录](docs/releases/v0.5.0-beta.3-local.md)。这些历史记录中的“不发布”描述对应取得本次 beta 发布授权之前，不代表已完成发布验证。

## 产品形态

- `L0 桌宠`：屏幕边缘常驻的像素眼镜小哥，无 Dock 图标干扰。
- `L1 即时交互`：通过双击桌宠或全局快捷键快速提问、确认读屏、获取简要结论。
- `L2 完整工作台`：查看完整答案、工具过程、历史会话和设置。
- `Agent 引擎`：由 Bun 编译为独立可执行文件，通过 JSONL RPC 与 PyQt6 壳层通信。

运行数据默认位于 `~/Library/Application Support/haochen/`，内置 Agent 引擎不借用全局 `~/.pi` 会话。Otty 诊断会检查相关集成配置元数据；用户显式确认的 Pi/OMP 集成安装可能在对应全局扩展目录新增文件，这是与内置引擎隔离的可选操作。

## 系统要求

- macOS
- 源码开发：Python 3.12、Node 22.19.0、Bun 1.4.0、uv
- 读屏能力：由用户按需授予“辅助功能”权限
- 看图能力：由用户按需授予“屏幕录制”权限

## 本地开发

```bash
make bootstrap
make check

# 使用 mock 引擎启动完整 App
HAOCHEN_MOCK=1 HAOCHEN_SKIP_ONBOARDING=1 \
  QT_QPA_PLATFORM=offscreen app/.venv/bin/python app/run_app.py
```

常用入口：

- `make check`：版本一致性、凭据扫描、lint、类型检查、ShellCheck、测试与覆盖率
- `make regressions`：协议、配置、视觉、对话、桌宠及壳层回归
- `make engine`：构建独立引擎
- `make package`：Developer ID 正式打包；legacy 分发需显式设置 `HAOCHEN_BUILD_MODE=legacy`

0.5 总览的原生检查需在 macOS 图形会话运行（不是 offscreen WebView）：

```bash
app/.venv/bin/python scripts/verify_dashboard_native.py
app/.venv/bin/python scripts/verify_dashboard_ui.py
```

冻结包的隔离自检入口为 `haochen.app/Contents/MacOS/haochen --dashboard-check --dashboard-check-output /tmp/haochen-dashboard-check-001`。输出目录必须尚不存在；自检只用自己创建的临时文件，不授予权限、启动引擎或读取用户资料。它是工程验证，不能代替用户实际连接后的验收。

## 仓库与版本

本仓库同时承载应用源码和 Homebrew Tap。`Casks/haochen.rb` 是默认通道；本次计划新增 `Casks/haochen@beta.rb` 作为独立测试通道。应用构建版本以根目录 `VERSION` 为唯一来源，采用 SemVer；不同分发通道可指向不同的已发布版本，不能用开发版 VERSION 覆盖稳定配方。发布流程见 [版本与发布规范](docs/versioning-and-release.md)，协作方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

v0.3.0 的交互升级方案见 [桌宠交互全面升级方案](docs/roadmap/v0.3.0-experience-redesign.md)；
v0.3.1 进一步修复并收敛了桌宠输入气泡的尺寸、留白和视觉层级，补齐了
待机、思考和警示三态的全身角色动作，并保持既有像素形象与肤色风格。
v0.3.2 为短气泡补齐关闭、发送与展开图标，悬停时暂停自动退场；同时明确
haochen 与用户称呼的身份边界，并支持从首启向导或设置安全配置自定义模型。
v0.3.4 汇总本轮验收：中性轻量气泡、统一模型连接生效流程、按提问绑定阅读目标、
真实读取阶段提示及无需削减正文/原图的读取器提速。详见 [里程碑发布说明](docs/releases/v0.3.4.md)。

## 安全与隐私

请不要在 Issue、日志或截图中提交 API Key、私钥、个人屏幕内容或本机会话数据。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可

本仓库目前未授予开源使用、复制或再分发许可。若未来开放源代码许可，会在仓库根目录增加明确的 `LICENSE` 文件。
