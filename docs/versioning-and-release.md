# 版本与发布规范

## 版本模型

haochen 使用 Semantic Versioning：

- `MAJOR`：数据格式、核心工作流或兼容性发生不可兼容变化。
- `MINOR`：向后兼容的产品能力或大幅交互升级。
- `PATCH`：向后兼容的 Bug、安全和发布修复。
- 预发布：`0.3.0-dev.1` → `0.3.0-beta.1` → `0.3.0-rc.1` → `0.3.0`。

根目录 `VERSION` 是唯一版本源；`scripts/version.py sync` 同步 Python 和引擎元数据。不要手改生成后的版本字段。

## 分支模型

采用轻量主干开发：

1. `main` 始终保持可测试、可构建。
2. 功能和修复从最新 `main` 创建短分支并通过 PR 合并。
3. 每个 PR 必须通过 CI、代码审查和对应的交互回归。
4. 发布标签 `vX.Y.Z` 必须指向包含该版本完整源码的 `main` 提交。

历史说明：仓库原来只承载 Homebrew Tap，因此 `v0.2.0` 标签指向旧 Cask 发布提交，而完整源码历史在 2026-09-08 合并进入 `main`。保留该标签以避免破坏现有 Release；从下一版本开始严格执行“标签指向完整源码”的规则。

## 发布门禁

发布候选必须满足：

- `make check` 全绿，覆盖率不低于既有门槛。
- 桌宠、完整窗口、首启、权限、崩溃恢复和多屏场景通过。
- UI 改动完成真机截图或录屏验收，不能只验证信号或控件存在。
- Secret scan、签名、Notarization、Staple 与 Gatekeeper 评估通过。
- DMG 文件名、App 版本、引擎版本、Cask 版本和 SHA-256 一致。
- `CHANGELOG.md` 和 Release Notes 完整，具备回滚说明。

## 发布步骤

```bash
# 1. 设置目标版本并同步元数据
printf '0.3.0\n' > VERSION
uv run python scripts/version.py sync

# 2. 验证
make check
make regressions

# 3. Developer ID 正式构建和公证
HAOCHEN_SIGNING_IDENTITY='Developer ID Application: …' \
HAOCHEN_NOTARY_PROFILE='haochen-notary' \
  make package

# 4. 由真实 DMG 生成根 Cask
uv run python scripts/version.py render-cask packaging/dist/haochen-0.3.0.dmg

# 5. 再次验证、提交、创建签名标签并发布 Release
make check
git tag -s v0.3.0 -m 'haochen v0.3.0'
```

发布后验证 Release 资产可下载、SHA-256 匹配，并在一台没有开发环境的 Mac 上执行 Homebrew 安装、首次启动和卸载回归。

## 0.2.0 例外

0.2.0 是发布者自签名的过渡版本，Cask 包含移除 quarantine 的兼容逻辑。该例外不得复制到下一正式版本；0.3.0 的发布阻断条件包括 Developer ID 和 Apple Notarization。
