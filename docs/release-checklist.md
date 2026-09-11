# v0.3.4 正式发布清单

2026-09-11 所有者亲自验收 rc.8 通过，确认本版为里程碑并授权同步 GitHub 与 Homebrew。正式版不追加功能；发布检查发现并修复慢机器上的详情滚动定位竞态，未改变界面设计。所有者明确选择继续沿用 legacy DMG + Homebrew；未经 Apple 公证，必须使用稳定自签身份，禁止回落 ad-hoc。

## 本轮交付顺序

rc.8 目标身份修复、真实进度提示、读取器复用和复杂 BBC 页面实测见 [读屏性能与正确性记录](reviews/v0.3.4-rc.8-reading-latency.md)。所有者已验收；本次发布不替换本机 App、不清理用户数据。

rc.7 固定原生对象、配置重启提示和当前读屏失败修复详见 [本轮验证记录](reviews/v0.3.4-rc.7-bound-reader.md)。不宣称所有第三方应用均已覆盖。

- [x] 每轮提问绑定窗口，确认前不读取正文；原生跨窗口读取与授权等待竞态回归。
- [x] legacy 候选版本、内嵌版本、DMG 一致，签名验证通过。
- [x] 交付候选包由所有者验收，不自动替换已安装 App、不清理用户数据。
- [x] 所有者亲自验收并授权正式发布。
- [ ] 其余二期体验仍待所有者确认；本轮仅实现已授权的窗口感知与固定阅读目标。

## 正式发布前的后续门禁

发布源提交为 `438d538cd60af453b9c764c69bafa9b0de59062e`（PR #10，云端 CI 通过后合并）。最终包从该干净 main 构建；Homebrew 更新仅在 Release 资产可下载后合入 main。

- [x] 本机 `make check`：472 项通过，覆盖率 83%。
- [x] `make regressions`：6/6 通过。
- [x] PR CI：macOS quality gate 通过（run `34609227251`）；未构建真实引擎的 CI 环境跳过 4 项，本机已实际执行。
- [x] 干净 main 重建引擎与 legacy DMG；稳定身份 `haochen Local Signing`。
- [x] `legacy-artifacts`：App 签名、DMG 完整性、版本、Cask SHA-256 一致。
- [ ] 发布 GitHub Release 资产并验证公开下载。
- [ ] 合入 Homebrew 更新并执行 `brew fetch --cask` 验证；不替换本机 App。

最终资产：`haochen-0.3.4.dmg`，SHA-256：`07e201c2e262a930cb74032d04df25d20280b38e2f5cf2f91c8c95240fc12011`。

发布检查额外修复的详情定位竞态：原事件顺序为定位计时器结束时滚动范围仍为 0，随后 `rangeChanged` 才给出完整历史范围。现在溢出内容未完成布局前保持定位待完成状态，保留后续定位；增加确定性回归。外观与交互功能未扩展。

1. 所有者验收通过后，将候选稳定为正式版本，完成 PR / CI。
2. 执行 make check、相关分层回归，构建最终 legacy 包。
3. 用最终 DMG 渲染 Cask：scripts/version.py render-cask --legacy。
4. 执行 scripts/release_preflight.sh legacy-artifacts，核对 DMG/Cask SHA、版本和签名。
5. 合并正式源码与 Cask，创建不可变标签及 Release，复核 Homebrew 安装。

legacy Cask 的 postflight_steps 延续既定 quarantine 处理。自签名不等于 Apple 公证；不得描述为 Developer ID 或 Notarized。

## 回滚

验收失败先修正候选并重新构建，不覆盖既有线上版本或资产。本机旧包移入系统废纸篓，保留恢复能力；不在可搜索目录保留额外 App，不删除配置、会话或钥匙串。

历史 v0.3.3 发布记录仍保存在 v0.3.3 标签中。

rc.3 的视觉统一、原生标题栏、人物锚点及安装验证见 [本轮工程记录](reviews/v0.3.4-rc.3-visual-acceptance.md)。所有者验收仍待完成。

rc.4 的单按钮连接、权限状态及原生检查见 [连接修复记录](reviews/v0.3.4-rc.4-connection-acceptance.md)。

rc.5 的初始化配置生效根因、统一流程、真实引擎首答回归及卸载交付见 [配置生效记录](reviews/v0.3.4-rc.5-config-activation.md)。本机旧 App 已卸载，由所有者自行安装新包。

rc.6 的提问时窗口感知、阅读目标固定、采集边界和实测证据见 [窗口阅读验收记录](reviews/v0.3.4-rc.6-screen-target.md)。本轮没有卸载或安装 App。

本轮证据见 [工程验证记录](reviews/v0.3.4-rc.1-engineering-check.md) 与 [rc.2 保存修复](reviews/v0.3.4-rc.2-keychain-save.md)。旧钥匙串可能包含绑定旧 cdhash 的分区限制，稳定自签身份不保证无感读取；只能由用户明确许可恢复，禁止自动改写或放宽 ACL。rc.2 修正了显式保存被错误静默化的问题：用户点击保存时可正常获得 macOS 授权，后台路径仍无弹窗。
