# 方案 B：自签受信任证书 —— 操作说明（用户/产品执行）

> 目的：让 haochen .app 有**稳定** code 签名身份 → macOS「辅助功能」授权按 DR 认 → 授权跨构建持久，不再每次重建都重授权。解决 ①读屏失败 ②授权弹窗不关（同根因：ad-hoc 重建换 DR）。

---

## 一步：跑脚本（只有最后一步需要输一次管理员密码）

```bash
cd "/Users/yangwz/Documents/workspace/haochen new"
bash packaging/setup-signing.sh
```

- 脚本会：生成自签证书（codeSigning EKU，3 年）→ 专用钥匙串 → 导入身份 → 设受信任。
- **唯一需要你输入的**：脚本末尾 `sudo security add-trusted-cert ...` 会提示**管理员密码**（按一次回车/输一次密码即可）。
- 完成后输出 `haochen Local Signing` 身份已就绪；钥匙串密码存 `packaging/signing.keychain-pw`（0600，build.sh 自动读）。

## 二步：用稳定身份重建

```bash
bash packaging/build.sh
```

- build.sh §5 检测到稳定身份会自动用 `haochen Local Signing` 签名（不再 ad-hoc）；产物 `packaging/dist/haochen.app` + `.dmg`。
- 可 `codesign -dvv dist/haochen.app | grep Authority` 确认是 `haochen Local Signing` 而非 adhoc。

## 三步：首次授权「当前构建」（一次性；之后跨构建持久）

1. 启动：`open dist/haochen.app`（或双击）。
2. 授权引导卡片弹出 → 点**「去授权」** → 系统弹窗点**「打开系统设置」** → 在 **系统设置 → 隐私与安全性 → 辅助功能** 打开 **haochen** 开关。
3. 回到 haochen 卡片点**「我已授权」** → 卡片关闭（此时当前构建已授权，DR 稳定）。
4. **验证**：问一句「看看我屏幕上是什么」→ 感知提示 → 「读吧/不读」→ 授权 → `read_screen` 工具卡 → 答案含当前窗口内容（读屏成功）。

## 四步：重启/跨构建持久验证（可选）

- 重启 App / 重新 `build.sh` 构建后再次启动：日志 `accessibility granted at startup: True`（授权跨构建仍在）。
- 读屏再次直接成功（不需要重新授权）。

---

## 排障

- 若授权卡片点「我已授权」仍报未授权：确认设置列表里 haochen 开关是开的；若开仍不报，点「暂不」→ 重启 App → 重试（个别情况需重启才能识别）。
- 若 `security find-identity -p codesigning` 找不到身份：重跑 `bash packaging/setup-signing.sh`，确认管理员密码那步成功。
- ad-hoc 与稳定身份不可混用：若你之前用 ad-hoc 授权过，删掉设置里旧的 haochen 勾选再重新走三步（TCC 按 DR 认，旧 DR 勾选对当前构建无效）。

## 附：技术背景

- macOS TCC「辅助功能」授权按 code 的 **designated requirement (DR)** 记录。ad-hoc `--sign -` 每次构建 DR 不同 → 旧授权不适用于新构建。
- 自签**受信任**证书 → 稳定 identity → 稳定 DR → 授权持久。
- 分发请用 Developer ID + 公证（总纲 §五 既定）；本方案用于本机/内测的持久授权。
