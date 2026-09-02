# v0.2.0 发布清单

> 状态（2026-09 更新）：所有者已知情选择沿用 0.1.x 的 legacy 发布模式（自签身份 + cask postflight 去隔离，不经 Apple 公证）。
> DMG 已按该模式构建并通过校验；cask 已推送 tap；tag v0.2.0 已推。**剩余唯一步骤：网页创建 Release v0.2.0 并上传 DMG 资产。**

## A. 已完成的验收（证据可复跑）

| 项目 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `make check`（版本/secret/ruff/pyright/shellcheck/pytest/coverage） | 全绿，241 passed，覆盖率 81% |
| 单元/安全/生命周期/会话/UI 流程 | `pytest tests/` | 241/241（两轮确认） |
| L1 引擎协议 | `python3 mock-engine/driver.py` | 45/45 |
| L2 配置自检 | selfcheck | 27/27 |
| 视觉扩展 | `bun run app/ext/test-visual-mode.ts` | 21/21 |
| L3 对话场景 | chat/verification/run_scenarios.py | 全部场景完成 |
| L3 桌宠场景 | pet/verification/run_scenarios.py | 0 FAIL |
| L3 P4 集成 | p4_integration.py | 28/28 |
| L4 打包（开发签名） | `HAOCHEN_BUILD_MODE=development bash packaging/build.sh` | App+DMG 构建成功，codesign 校验通过，版本一致（App 0.2.0 / build 1 / engine 0.2.0-dev.1） |
| 全量回归 | `bash tests/run_all_regressions.sh` | PASS=6 FAIL=0 |
| 仓库凭据扫描 | `scripts/check_secrets.py` + Git 全历史扫描 | 无泄露 |
| release 缺凭据失败路径 | 无凭据跑 release 构建 | 正确拒绝（不静默降级） |

## B. 发布前必须由凭据所有者完成（外部动作）

1. 轮换/吊销旧凭据（见 `credential-rotation-required.md`）：旧自签私钥/P12/Keychain 口令、泄露的第三方 API Key。
2. 准备 Apple Developer ID Application 证书（导入 Keychain）。
3. `xcrun notarytool store-credentials <profile>` 存入公证凭据。

## C. 凭据就绪后的发布流程

```bash
# 1. 正式构建（自动：hardened runtime 签名 → 公证 → staple App → 签名 DMG → 公证 staple → spctl）
export HAOCHEN_SIGNING_IDENTITY='Developer ID Application: … (TEAMID)'
export HAOCHEN_NOTARY_PROFILE='<profile>'
HAOCHEN_BUILD_MODE=release bash packaging/build.sh

# 2. 产物校验
codesign --verify --deep --strict packaging/dist/haochen.app
spctl --assess --type execute --verbose=2 packaging/dist/haochen.app
xcrun stapler validate packaging/dist/haochen.app
xcrun stapler validate packaging/dist/haochen-0.2.0.dmg
shasum -a 256 packaging/dist/haochen-0.2.0.dmg

# 3. Cask 渲染（读取根 VERSION 与真实 SHA-256）
python3 scripts/version.py render-cask packaging/dist/haochen-0.2.0.dmg

# 4. 干净机器安装验收（docs/qa-script.md 全量走查，含升级保留配置）

# 5. 发布：打签名 tag v0.2.0、上传 DMG 到 GitHub Release、推送 Cask
git tag -s v0.2.0 -m 'haochen v0.2.0'
```

## D. 人工验收项（无头 CI 无法覆盖，发布时在真机执行）

- VoiceOver 朗读关键控件；纯键盘完成发送/确认/会话管理主流程。
- 系统设置开启 Reduce Motion → 确认动画跳过。
- 副屏拖动桌宠 → 气泡锚定同屏。
- 屏幕录制授权后重启生效；TCC 授权跨升级保持。
- 干净机器双击安装：Gatekeeper 放行（无右键绕过）；升级安装保留会话与配置。

## E. 回滚说明

- 发布失败：不删 tag，直接发 patch 版本（0.2.1）修复；DMG 下载链接指向 Release 资产。
- 用户侧回滚：Cask `zap trash` 清数据后安装旧版本 DMG（历史版本按 SemVer 留存在 Release）。
- 配置迁移可回滚：Keychain 迁移保留备份能力（corrupt 备份机制）；会话数据从未破坏性改写。
