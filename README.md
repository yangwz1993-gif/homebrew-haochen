# haochen

haochen 是一款面向 macOS 的独立桌面 AI 伙伴：常驻桌面、按需读取当前窗口、调用本机工具，并用轻量气泡或完整工作台返回结果。

[![CI](https://github.com/yangwz1993-gif/homebrew-haochen/actions/workflows/ci.yml/badge.svg)](https://github.com/yangwz1993-gif/homebrew-haochen/actions/workflows/ci.yml)
[![version](https://img.shields.io/badge/version-0.3.4-3a7d5c)](https://github.com/yangwz1993-gif/homebrew-haochen/releases/tag/v0.3.4)
[![platform](https://img.shields.io/badge/platform-macOS-2b2b22)](#系统要求)

## 当前状态

- 稳定版本：`0.3.4`（所有者验收通过的里程碑版）
- 自动化测试：471 项，整体覆盖率 83%
- 分发：GitHub Release + Homebrew Cask

0.3.4 经所有者明确选择继续使用发布者自签名；Homebrew Cask 会移除该 App 的
quarantine 属性。它没有经过 Apple Developer ID 签名或 Apple 公证，直接下载 DMG
可能被 Gatekeeper 拦截，建议只使用下面的 Homebrew 安装方式。

## 安装

```bash
brew tap yangwz1993-gif/haochen
brew install --cask haochen
open -a haochen
```

已通过 Homebrew 安装的用户：`brew update && brew upgrade --cask haochen`。

首次使用时，按向导确认 haochen 如何称呼你，并选择 DeepSeek 或填写自定义 OpenAI 兼容模型的 URL、模型 ID 和 API Key。Key 存储在 macOS Keychain；辅助功能和屏幕录制权限只在相关能力需要时申请。

## 产品形态

- `L0 桌宠`：屏幕边缘常驻的像素眼镜小哥，无 Dock 图标干扰。
- `L1 即时交互`：通过双击桌宠或全局快捷键快速提问、确认读屏、获取简要结论。
- `L2 完整工作台`：查看完整答案、工具过程、历史会话和设置。
- `Agent 引擎`：由 Bun 编译为独立可执行文件，通过 JSONL RPC 与 PyQt6 壳层通信。

运行数据默认位于 `~/Library/Application Support/haochen/`，不会读写全局 `~/.pi`。

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

## 仓库与版本

本仓库同时承载应用源码和 Homebrew Tap，根目录 `Casks/haochen.rb` 是 Homebrew 的发布入口。版本以根目录 `VERSION` 为唯一来源，采用 SemVer；发布流程见 [版本与发布规范](docs/versioning-and-release.md)，协作方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

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
