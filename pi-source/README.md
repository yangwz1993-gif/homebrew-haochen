# haochen 引擎源码（fork 自 pi）

> 本目录是 haochen 的**自研引擎源码库**，fork 自官方 pi 仓库，用于开发我们自己的应用。
> 来源：`earendil-works/pi` 官方仓库，tag `v0.84.3`。
> 说明：**不依赖 npm 安装的 pi 包**。我们拥有这份代码，可自行修改内核（换默认模型、加功能、加 provider 集成），避免与本机全局 pi 冲突、锁定版本行为。

## 这是个什么

pi 官方仓库是 monorepo：
```
packages/
├── coding-agent/   # 主包：CLI + RPC 入口（bin: dist/bundle/cli.js）—— haochen 引擎的主入口
├── agent/ ai/ client/ protocol/ server/ session-backends/ telemetry/ tui/   # 引擎内部子包
```

haochen 的桥（Qt 桌宠 + TUI）通过 `coding-agent` 的 RPC 模式（`--mode rpc`）调用它当引擎。

## 为什么 fork 而不是 npm 依赖

1. **避免与本机冲突**：全局 pi / npm 包版本变动不会影响 haochen。
2. **可改内核**：像 Prime Agent fork Pi 一样，我们能在源码层改默认模型、加自己功能、加 provider 集成。
3. **锁定行为**：版本、行为完全由我们控制，不再受上游 npm 发布节奏影响。

## 当前状态

- 源码已 fork 落地（v0.84.3）。
- **构建**：`coding-agent` 的 `build` 需要工具链（`tsgo`、`bun`）+ monorepo 子包（agent/ai/client/tui/protocol/server/telemetry 等）逐一构建后 bundle。工程量大（依赖 monorepo 内部互链）。
- 在完整构建跑通前，haochen 仍可暂时复用现有可运行的引擎 bundle 过渡；最终目标是用本目录源码构建出我们自己的 `haochen` 引擎。

## 使用

（构建/运行方式待确定后补全）
