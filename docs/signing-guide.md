# Developer ID 签名与 Apple 公证

v0.2.0 起禁止自签分发、App 内生成签名身份、明文保存钥匙串口令以及移除 quarantine。正式发布只接受 Apple Developer ID Application 签名和 Apple 公证。

## 准备（发布者执行）

1. 把 Developer ID Application 证书导入 macOS Keychain；私钥不得进入项目目录。
2. 使用 `xcrun notarytool store-credentials <profile>` 把公证凭据保存到 Keychain；CI 中则从受保护 Secret 创建临时 Keychain/profile。
3. 设置非敏感引用：

```bash
export HAOCHEN_SIGNING_IDENTITY='Developer ID Application: … (TEAMID)'
export HAOCHEN_NOTARY_PROFILE='haochen-notary'
# 可选：export HAOCHEN_KEYCHAIN=/path/to/temporary-ci.keychain-db
```

不要把 identity 对应私钥、公证 API key、Apple ID app-specific password 或 Keychain 密码写入 `.env`、脚本、日志和 Git。

## 正式构建

```bash
HAOCHEN_BUILD_MODE=release bash packaging/build.sh
```

构建会：

1. 对嵌套 Mach-O 和 App 进行 hardened runtime + timestamp 签名；
2. 公证并 staple App；
3. 创建并签名 DMG；
4. 公证、staple 并用 Gatekeeper 验证 DMG。

缺少 Developer ID identity 或 notary Keychain profile 时，release 构建必须失败。仅本机测试可显式运行：

```bash
HAOCHEN_BUILD_MODE=development bash packaging/build.sh
```

开发产物使用 ad-hoc 签名，不得上传 Release、生成正式 Cask 或对外分发。

## 发布验收

```bash
codesign --verify --deep --strict packaging/dist/haochen.app
spctl --assess --type execute --verbose=2 packaging/dist/haochen.app
xcrun stapler validate packaging/dist/haochen.app
xcrun stapler validate packaging/dist/haochen-*.dmg
```

Cask 由 `scripts/version.py render-cask <dmg>` 根据根 `VERSION` 和真实 SHA-256 生成，禁止 `postflight` 清除 quarantine。

## 已泄露凭据处置

旧工程曾保存自签私钥、P12、Keychain 口令和一个第三方模型 API Key。它们都必须视为已泄露，由用户在对应平台轮换/吊销。新仓库不得复用这些凭据；完成外部轮换前不能宣称发布安全闭环。
