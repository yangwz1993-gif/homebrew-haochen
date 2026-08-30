# haochen 安装与配置指引

## 安装

```bash
brew tap yangwz1993-gif/homebrew-haochen
brew install --cask haochen
open -a haochen
```

只允许安装通过 Developer ID 签名、Apple 公证且 SHA-256 与 Cask 一致的正式产物。不要移除 quarantine，不要关闭 Gatekeeper。

## 配置模型

在 haochen 设置页选择 provider/模型，并把**用户自己提供的** API Key 保存到安全配置入口。不得在聊天、脚本、文档或仓库中粘贴真实凭据，也不要由 Agent 直接改写认证文件。

首次使用读屏时，由用户在“系统设置 → 隐私与安全性”中按需授权辅助功能或屏幕录制；任何程序都不得代替用户绕过系统授权。

## 验证

- `codesign --verify --deep --strict /Applications/haochen.app`
- `spctl --assess --type execute --verbose=2 /Applications/haochen.app`
- 启动后使用设置页的连接测试验证用户自己的 Key。
