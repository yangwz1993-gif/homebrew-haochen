# v0.3.4-rc.5 正式发布清单

当前仅为本机验收候选，尚未正式发布。所有者明确选择沿用 legacy DMG + Homebrew；本轮由所有者亲自验收，不安排验收 agent。未经 Apple 公证，必须使用稳定自签身份，禁止回落 ad-hoc。

## 本轮交付顺序

- [x] 统一配置生效流程，验证首次填 Key 到真实引擎首答的完整链路。
- [x] legacy 候选版本、内嵌版本、DMG 一致，签名验证通过。
- [x] 停止并卸载本机 App，清空运行日志，交付新安装包由所有者安装验收；保留配置、API Key、会话及系统授权。
- [ ] 所有者亲自验收。
- [ ] 所有者明确通过后再启动二期；快捷键唤起纳入二期。

## 正式发布前的后续门禁

当前 Cask 继续指向已发布版本，不把尚未验收的候选当作线上交付。

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

本轮证据见 [工程验证记录](reviews/v0.3.4-rc.1-engineering-check.md) 与 [rc.2 保存修复](reviews/v0.3.4-rc.2-keychain-save.md)。旧钥匙串可能包含绑定旧 cdhash 的分区限制，稳定自签身份不保证无感读取；只能由用户明确许可恢复，禁止自动改写或放宽 ACL。rc.2 修正了显式保存被错误静默化的问题：用户点击保存时可正常获得 macOS 授权，后台路径仍无弹窗。
