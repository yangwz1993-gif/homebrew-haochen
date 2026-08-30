# Changelog

本项目从 `0.2.0-dev.1` 起遵循 [Semantic Versioning](https://semver.org/)；正式发布记录按 Keep a Changelog 维护。

## [Unreleased]

### Added
- 建立独立的新版本源码基线、Git 管理和发布规划。
- 新增 v0.2.0 开发方案与缺陷修复验收门禁。
- 建立 Python/Node/Bun 版本锁、uv/npm 锁文件安装和统一 `make check` 质量入口。
- 新增版本一致性、secret、ruff、pyright、shellcheck、pytest 与覆盖率检查。
- 以根 `VERSION` 驱动 App、引擎清单、DMG/Info.plist 与 Cask 发布模板。

### Security
- 新版本基线不包含旧工程中的签名私钥、证书、口令或本地认证数据。

## [0.2.0-dev.1] - 开发中
- 从旧工程 `haochen_new` 的 0.1.10 源码建立干净基线。
