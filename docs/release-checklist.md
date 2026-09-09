# v0.3.0 正式发布清单

> 更新：2026-09-09。体验候选已通过第 16 轮独立用户验收；正式发布仍被
> Developer ID Application 证书和 Apple 公证 profile 阻断。v0.3.0 不沿用
> v0.2.0 的自签名/quarantine 例外。

## 当前可证实状态

| 门禁 | 当前证据 | 状态 |
|---|---|---|
| 独立用户验收 | `docs/reviews/v0.3.0-aesthetic-review-16.md` | PASS |
| 代码质量 | `make check`：335 tests，coverage 84% | PASS |
| 分层回归 | `make regressions`：6/6 | PASS |
| PR | PR #6，提交 `78b30cf`，远端 macOS quality gate 通过 | PASS |
| 本地候选 | `haochen-0.3.0-dev.1.dmg`，仅 ad-hoc 签名 | 仅供本机试用 |
| Developer ID | Keychain 仅有 `haochen Local Signing` | BLOCKED |
| 公证与 Gatekeeper | 开发候选无公证票据，`spctl` 拒绝 | BLOCKED |

开发候选 SHA-256：
`b3e3889c222d3749f583d5d3c56d976f1ac6c1fdafd2e0ef5086621c76e1c41c`。
它不是公开发行物，禁止上传 GitHub Release 或写入正式 Cask。

## 凭据所有者需要完成

1. 在本机 Keychain 安装有效的 `Developer ID Application: … (TEAMID)` 证书与私钥。
2. 在本机运行 `xcrun notarytool store-credentials <profile>` 保存 Apple 公证凭据。
3. 只提供证书显示名称和 profile 名称；不得把私钥、密码或 API key 发到聊天、仓库或日志。
4. 设置非敏感引用后执行只读自检：

```bash
export HAOCHEN_SIGNING_IDENTITY='Developer ID Application: … (TEAMID)'
export HAOCHEN_NOTARY_PROFILE='<profile>'
bash scripts/release_preflight.sh credentials
```

## 凭据到位后的发布顺序

每一步失败都停止，不允许自动降级到 `legacy` 或 `development`：

1. 合并已通过检查的 PR #6 到 `main`，在 `main` 上将 `VERSION` 从
   `0.3.0-dev.1` 升为 `0.3.0`，运行 `scripts/version.py sync`，补齐
   `docs/CHANGELOG.md` 和 Release Notes，并提交版本变更。
2. 执行源码门禁：

   ```bash
   bash scripts/release_preflight.sh source
   make check
   make regressions
   ```

3. 正式构建。`packaging/build.sh` 会依次完成 hardened runtime 签名、App 公证与
   staple、DMG 签名、公证与 staple，以及 Gatekeeper 验证：

   ```bash
   HAOCHEN_BUILD_MODE=release bash packaging/build.sh
   ```

4. 对产物执行独立只读门禁：

   ```bash
   bash scripts/release_preflight.sh artifacts \
     packaging/dist/haochen.app \
     packaging/dist/haochen-0.3.0.dmg
   ```

5. 在没有开发环境的 Mac 上，从 DMG 首次安装并逐项验证：Gatekeeper 直接放行、
   首次引导、点击/右键人物、发送与停止、简答/详情、读屏拒绝与授权、冷启动历史恢复、
   升级保留配置、卸载。保存系统版本、步骤、截图和最终结论。
6. 用真实 DMG 生成正式 Cask，再次确认没有 `postflight`、`xattr` 或 quarantine 绕过：

   ```bash
   uv run python scripts/version.py render-cask packaging/dist/haochen-0.3.0.dmg
   make check
   ```

7. 审查最终提交后创建签名标签 `v0.3.0`，上传同一个已验证 DMG，核对线上下载文件的
   SHA-256，再更新 Homebrew Cask。标签、Release 和 Cask 在所有门禁完成前不得创建。

## 放行定义

只有以下条件同时成立，才能对外称为“可给大家使用”：

- 第 16 轮或更晚的独立用户验收为 PASS；
- `main` 的稳定版本源码、测试、回归和 CI 全绿；
- App 与 DMG 均为 Developer ID Application 签名，公证票据有效；
- `spctl` 对 App 与 DMG 都放行；
- 干净机器安装、首次使用、升级和卸载回归通过；
- Git tag、GitHub Release、DMG、Cask 版本与 SHA-256 完全一致。

## 回滚

- 发布前任一门禁失败：不创建 tag/Release/Cask，修复后重建整个正式产物。
- 发布后发现问题：保留不可变的历史资产，以 patch 版本修复；不得移动或覆盖既有标签。
- Cask 暂停指向有问题的版本，并在 Release Notes 明确受影响范围与替代版本。
