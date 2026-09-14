# v0.6.2-beta.2 本地验收清单

> 在 0.6.2-beta.1 基础上继续：内网模型（CodeWiz）应用内接入、Hi 精准跳转、hi 探测覆盖、连接卡片交互、预装 Hi 技能。**所有者明确授权内部 Homebrew 分发**（自签、未经 Apple 公证，仅面向内部同事试用）；**未授权面向公众的公证发布**。上一轮记录见 [v0.6.2-beta.1 之前的清单历史]。

所有者明确选择继续沿用 legacy DMG 路线并**授权更新内部 Homebrew tap（GitHub Release + Cask）供内部安装**；本包未经 Apple 公证，必须使用稳定自签身份（`haochen Local Signing`），禁止回落 ad-hoc，也不得描述为 Developer ID 或 Notarized。打包与签名在具备该身份的所有者机器上完成，不在无身份的新机上回落 ad-hoc。面向公众的公证渠道（Developer ID + notarization）本轮仍未授权、未执行。

## 本轮边界

- 源码在 `feat/v0.5-dashboard` 开发；Cask 发布到 tap 的 `main` 分支（brew 只读 main）。
- 内部分发：GitHub Release 传 `haochen-<version>.dmg`，Cask 由 `scripts/version.py render-cask --legacy` 从该 DMG 渲染，sha256 自动对齐。
- 构建完成后退出并移除现有 App，再安装新包、验证实际体验；旧数据可从备份恢复。
- 保留原配置、会话、日志、钥匙串和系统授权；权限（辅助功能/屏幕录制）由用户各自授权，Cask 只去 quarantine。

## 本机交付门禁

- [x] 完成 `make check`：Python 813 passed / 1 opt-in skipped，Node 42/42。
- [x] 用 legacy 模式重建 DMG，`scripts/version.py render-cask --legacy` 生成 Cask，sha256 与 DMG 一致。
- [x] 核对 App / 内嵌 VERSION / DMG 版本一致（SemVer 0.6.2-beta.2，Apple 0.6.2 / build 2，扩展 0.6.2.2）。
- [x] 替换安装：移除旧 App，安装并启动新包；稳定自签身份跨重装保权限。
- [x] 内网模型应用内校验实测（真实端点 `/chat/completions` 200）、Hi 精准跳转端到端实测（单聊/群聊/应用号）。
- [ ] 所有者对本内部版本的最终体验验收（进行中）。

## 面向公众的正式发布门禁（本轮不执行）

1. 获得所有者对**公开发布**的确认，确定正式版本并完成 PR / CI。
2. 构建最终 **release**（Developer ID + hardened runtime + notarization）包，而非 legacy。
3. 执行 `scripts/release_preflight.sh legacy-artifacts` 或对应的公证校验，核对签名/公证/版本。
4. 经确认后合并、创建不可变标签及公开 Release。

legacy Cask 的 quarantine 处理沿用既定路线，不代表 Apple 公证。验收失败仅修改候选，不覆盖既有线上版本。

上一稳定里程碑的授权与验证记录见 [v0.3.4 清单](release-checklist-v0.3.4.md)。
