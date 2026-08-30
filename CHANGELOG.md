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

### Security
- 新版本基线不包含旧工程中的签名私钥、证书、口令或本地认证数据。
- 远程图片仅允许解析到公网地址的 HTTPS/443，并在每次重定向时重新校验；拒绝本机、私网、link-local、非法 MIME 和超限响应。

## [0.2.0-dev.1] - 开发中
- 从旧工程 `haochen_new` 的 0.1.10 源码建立干净基线。
