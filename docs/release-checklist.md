# v0.3.2 正式发布清单

> 更新：2026-09-09。所有者明确选择 v0.3.2 延续上一版 legacy DMG + Homebrew
> Cask 分发：App 使用稳定的发布者自签身份，不经过 Apple 公证；Cask 安装后移除
> quarantine。不得把该产物描述为 Developer ID、Notarized 或 Gatekeeper 认证版本。

## 发布门禁

| 门禁 | 证据 | 状态 |
|---|---|---|
| 独立用户验收 | 前 2 轮质疑均已修复；第 3 轮等待同一独立 agent 复验 | 进行中 |
| 代码质量 | `make check`：358 tests，coverage 83% | PASS |
| 分层回归 | `make regressions`：6/6 | PASS |
| PR | [#8](https://github.com/yangwz1993-gif/homebrew-haochen/pull/8)；首轮 CI PASS，新修复提交后重跑 | 进行中 |
| 发布模式 | 所有者 2026-09-09 明确选择 legacy | 已授权 |
| 签名 | `haochen Local Signing`，门禁确认不是 ad-hoc | PASS |
| DMG / Cask | 第 3 轮候选 `f8aea229…a141d`；legacy preflight PASS，独立复验后封板 | 进行中 |
| Homebrew 安装 | 从线上 Release 安装、启动、升级与卸载 | 待发布复核 |

## 风险边界

- Homebrew 安装路径通过 Cask 的 `postflight_steps` 移除 App quarantine，延续上一版机制。
- 直接下载 DMG 可能被 macOS Gatekeeper 拦截；README 与 Release Notes 必须明确提示使用 Homebrew。
- 自签名只能证明构建前后使用了同一发布者身份，不能替代 Apple 对开发者身份和软件的公证。
- `development` 模式产生的 ad-hoc 包绝不允许上传；legacy 构建也必须通过独立门禁。
- 默认 `release` 模式继续强制 Developer ID 与 Apple 公证，不能在失败时静默降级。

## 发布步骤

1. 将版本稳定为 `0.3.2`，同步 Python、引擎与锁文件，完成 CHANGELOG/README。
2. 执行完整验证并确认 PR CI 通过：

   ```bash
   make check
   make regressions
   ```

3. 使用稳定自签身份构建，禁止回落为 ad-hoc：

   ```bash
   HAOCHEN_BUILD_MODE=legacy bash packaging/build.sh
   ```

4. 用真实 DMG 的 SHA-256 生成唯一发布 Cask：

   ```bash
   uv run python scripts/version.py render-cask --legacy \
     packaging/dist/haochen-0.3.2.dmg
   ```

5. 运行 legacy 产物门禁；它会验证 App/内嵌版本、稳定自签身份、拒绝 ad-hoc、DMG
   完整性、Cask 版本、SHA-256、quarantine 处理和 Ruby 语法：

   ```bash
   bash scripts/release_preflight.sh legacy-artifacts \
     packaging/dist/haochen.app \
     packaging/dist/haochen-0.3.2.dmg \
     Casks/haochen.rb
   ```

6. 提交稳定版本与 Cask，复跑 `make check` 并等待远端 CI 通过；将 v0.3.2 PR 合并到
   `main`。标签必须指向包含完整源码和最终 Cask 的 `main` 提交。
7. 创建不可变标签 `v0.3.2` 和 GitHub Release，上传门禁验证过的同一个 DMG；Release
   Notes 明确“未经 Apple 公证，建议使用 Homebrew 安装”。
8. 从线上渠道复核：

   ```bash
   brew tap yangwz1993-gif/haochen
   brew install --cask haochen
   open -a haochen
   ```

   验证首次引导、点击/右键人物、发送与停止、简答/详情、读屏拒绝与授权、冷启动历史、
   覆盖升级、`brew uninstall --cask haochen`。保存系统版本、步骤和结论。

## 放行定义

只有以下条件同时成立，才能称为“v0.3.2 legacy Homebrew 版已交付”：

- v0.3.2 修复后最终 legacy 包独立用户验收 PASS；
- `main` 稳定版本源码、本地检查、回归和远端 CI 全绿；
- App 使用 `haochen Local Signing`，不是 ad-hoc；
- DMG、App、内嵌版本、Cask、标签和 Release 全为 `0.3.2`；
- 本地与线上 DMG SHA-256 等同 Cask；
- Homebrew 安装、首次启动、升级与卸载复核通过；
- README、Cask、Release Notes 均清楚披露未经 Apple 公证。

## 回滚

- 发布前任一门禁失败：不创建标签和 Release，修复后完整重建。
- 发布后发现问题：保留既有标签和资产，以 patch 版本修复，禁止覆盖历史文件。
- 立即从 Cask 撤回有问题的版本，并在 Release Notes 标明影响和替代方案。
