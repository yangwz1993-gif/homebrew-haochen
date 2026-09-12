# v0.5.0-beta.1 本地验收清单

本轮所有者授权开发并交付本机测试，尚未验收通过，尚未授权公开发布 0.5。所有者明确选择继续沿用 legacy DMG 路线；本包未经 Apple 公证，必须使用稳定自签身份，禁止回落 ad-hoc，也不得描述为 Developer ID 或 Notarized。

## 本轮边界

- 源码在 `feat/v0.5-dashboard` 开发，不直接改远程 main。
- 交付本地 DMG 与启用/验收说明，不自动卸载或替换现有 App。
- 保留原配置、会话、日志、钥匙串和系统授权；不自动安装浏览器扩展或 Otty hooks。
- 飞书延期；其他连接的权限、未配置与不支持状态必须真实可见。
- 不更新 GitHub Release、标签或 Homebrew Cask，不以历史发布授权代替本版验收。

## 本机交付门禁

- [x] 完成 make check、生产 WebView 操作回归、原桌宠与聊天回归。
- [x] 使用 legacy 模式构建，核对 App / 内嵌 VERSION / DMG 版本一致（Apple 数字版本 0.5.0，SemVer 0.5.0-beta.1）。
- [x] 完成冻结包自检、稳定签名验证和 DMG 完整性校验。
- [x] 准备安装包与启用/验收说明，由所有者从实际安装开始验收。
- [ ] 所有者亲自验收通过（尚未完成）。

连接器代码与隔离测试通过不等于用户账号已授权；Chrome 实际站点、Otty hooks、日历授权与多屏实机体验仍需要用户验收。

## 未来正式发布门禁（本轮不执行）

1. 获得所有者对本版本的验收与公开发布确认，再确定正式版本并完成 PR / CI。
2. 构建最终 legacy 包；用 `scripts/version.py render-cask --legacy` 从最终 DMG 生成配方。
3. 执行 `scripts/release_preflight.sh legacy-artifacts`，核对 DMG/Cask SHA、版本和稳定签名。
4. 经确认后合并源码与 Cask、创建不可变标签及 Release，复核 Homebrew 安装。

legacy Cask 的 quarantine 处理沿用既定路线，不代表 Apple 公证。验收失败仅修改候选，不覆盖既有线上版本。

上一稳定里程碑的授权与验证记录见 [v0.3.4 清单](release-checklist-v0.3.4.md)。
