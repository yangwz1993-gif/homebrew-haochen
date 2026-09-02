# Changelog

本项目从 `0.2.0-dev.1` 起遵循 [Semantic Versioning](https://semver.org/)；正式发布记录按 Keep a Changelog 维护。

## [Unreleased]

### Added
- 建立独立的新版本源码基线、Git 管理和发布规划。
- 新增 v0.2.0 开发方案与缺陷修复验收门禁。
- 建立 Python/Node/Bun 版本锁、uv/npm 锁文件安装和统一 `make check` 质量入口。
- 新增版本一致性、secret、ruff、pyright、shellcheck、pytest 与覆盖率检查。
- 以根 `VERSION` 驱动 App、引擎清单、DMG/Info.plist 与 Cask 发布模板。

### Fixed
- 会话删除仅允许操作私有会话目录内的直接 `.jsonl` 文件，并拒绝路径穿越、越界绝对路径和符号链接。
- 删除当前会话前必须确认已切换到新会话；删除失败会保留并切回原会话。
- 会话删除增加二次确认、可见错误和私有回收站撤销能力。
- 引擎停止改为后台回收，UI 不再同步等待；SIGTERM 超时后强杀整个进程组，避免孤儿进程。
- RPC 请求加入 5 秒超时和崩溃/停止统一结算，坏 JSON、半行输出与 stderr 以无正文摘要写入轮转诊断日志。
- supervisor 增加 30 秒健康探针；无响应时进入可见的自动重启流程。
- 当前会话改由共享 SessionCoordinator 原子持久化，成功切换立即更新，切换失败保持原会话；重启恢复不再依赖滞后的探针状态。
- 大窗口和气泡共用一条跨入口 FIFO 输入队列；消息直到引擎确认写入会话后才出队。
- 崩溃时未确认消息以 0600 草稿保留且不自动重复发送，完整窗口提供“重新发送/取消”；坏队列文件会先备份。
- 流式渲染按 50ms 节流；仅在用户位于底部时自动跟随，回看时保持位置并提供“回到最新”。
- 两入口排队消息均可见、可单独取消；工具卡覆盖运行/成功/失败/取消状态，支持取消运行中工具、失败后重试和打开 write/edit 产物，并显示耗时。

### Security
- 新版本基线不包含旧工程中的签名私钥、证书、口令或本地认证数据。
- 远程图片仅允许解析到公网地址的 HTTPS/443，并在每次重定向时重新校验；拒绝本机、私网、link-local、非法 MIME 和超限响应。
- 用户提问不再写入 `last-user-text.txt`，仅以 0600 文件保存派生的看图意图 boolean，并自动删除旧版明文 sidecar。
- API Key 保存至 macOS Keychain；`auth.json` 仅保留 `$HAOCHEN_*_API_KEY` 引用，写入前先通过固定安全端点验证，失败不覆盖旧 Key。
- 首次启动改为单一可续办向导（欢迎→Key 验证→按需权限→试问），不再读取全局 `~/.pi` 凭据、不再同时弹出多个系统授权页面。
- 应用数据目录统一修复为 0700，认证、会话回收、日志与运行 sidecar 统一为 0600；引擎子进程使用 0077 umask。
- 删除 App 内自签/重签与本地口令流程；release 构建强制 Developer ID、hardened runtime、timestamp、公证和 staple。
- Homebrew Cask 不再移除 quarantine；开发版只能显式 ad-hoc 构建且禁止对外分发。
- 清理旧工程遗留的明文模型凭据与 Keychain 口令文档，并加强 secret scan 规则。

## [0.2.0-dev.1] - 开发中
- 从旧工程 `haochen_new` 的 0.1.10 源码建立干净基线。
