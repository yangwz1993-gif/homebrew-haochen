# 修复：重装后录屏授权 / Chrome 桥接总是失效

## 为什么会失效（一句话）
之前的包是 **ad-hoc 临时签名**，每次重打包代码签名（CDHash）都变。macOS 的「录屏 / 辅助功能」授权和 Chrome 本机桥接都**绑定在签名上**，签名一变 → 系统里授权还亮着，但对不上当前二进制 → 被拒。

## 一劳永逸的修法
建一个**固定的自签名身份**，以后重打包签名不变，授权和桥接都不再掉。

## 你要跑的（一行）
```bash
bash "/Users/yangwenzhu1/Desktop/111Workspace/260913_haochen继续开发/scripts/setup-local-signing.sh"
```
- 只**新增**一个自签证书到**独立钥匙串**，不碰你的登录钥匙串、不读任何密钥。
- 跑完最后打印 `✅ 成功 … haochen Local Signing` 即可。
- 中途若弹一次钥匙串授权框，点「允许 / 始终允许」。

## 跑完之后（我来做）
1. 我用这个固定身份 `legacy` 模式**重打包 + 重装** `/Applications/haochen.app`。
2. 你在 **系统设置 → 隐私与安全性 → 录屏**：删掉旧的 haochen 条目（−）→ 重新添加当前 app（+）→ **完全退出并重开 haochen**。
3. 之后我再重打包，签名不变，**录屏授权和 Chrome 桥接都不会再掉**。

（对应脚本：`scripts/setup-local-signing.sh`）
