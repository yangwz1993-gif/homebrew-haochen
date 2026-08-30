# P8 方案 B 前端化（App 内「一键修复签名权限」）— 评估与实现方案

> 日期：2026-08-26 ｜ 作者：kimi（研发）｜ 依据：产品要求「用户不满反复授权，把方案 B 做成前端一键」
> 状态：**方案待产品确认**。核心结论：**能做成 App 内一键**（密码框是 macOS 安全机制，无法完全免密，但封装为 App 内一次系统密码框，无需用户跑终端）。

---

## 一、结论：能做成一键（关键澄清）

- **能**在 App 内放一个「一键修复签名权限」按钮，点击后：
  - 弹出 **macOS 标准管理员密码框**（`osascript do shell script ... with administrator privileges`）——这是系统安全机制，**无法完全免密**（产品已理解），但用户是在 App 内输一次密码，**不需要跑终端命令**。
  - 密码通过后，自动完成：① 生成自签**稳定签名身份**（openssl+security）② `security add-trusted-cert`（admin）③ `codesign` 用稳定身份**重签当前 haochen.app**（DR 固定）。
  - 完成后引导**重启 App + 授权一次**（辅助功能+屏幕录制），此后**重建/升级同 DR → 授权持久、无需再授权、无需跑终端**。
- `do shell script ... with administrator privileges` 是 mac 标准做法（弹系统密码框的 Shell 脚本）。App 内调用即封装为一次系统密码框，而不是让用户开终端粘命令。

## 二、为什么这样能持久（回顾根因）

- 反复授权的根因 = **ad-hoc 签名每次重建换 DR** → TCC 授权按 DR 认 → 旧授权对不上。
- 方案 B = 用**稳定签名身份**（自签受信任证书）→ DR 恒定 → TCC 授权持久。
- App 内一键 = 生成该稳定身份 + 重签当前 app，把 DR 固定下来。之后构建/升级沿用同一身份 → 授权不再丢。

## 三、实现方案

### 1) 后端模块 `app_signing_repair.py`
- `ensure_admin_script(script) -> bool`：封装 `osascript -e 'do shell script "<script>" with administrator privileges'`（触发系统密码框；`-k` 不在命令行带密码，靠系统弹窗）。
- `generate_identity()`：openssl 生成自签证书（codeSigning EKU，3 年）→ p12（legacy）→ 专用钥匙串（`~/Library/Keychains/haochen-signing.keychain-db`，密码由 App 生成存 `packaging/signing.keychain-pw` 或 Keychain）→ `security set-key-partition-list`（免弹窗访问）。
- `trust_cert()`：`security add-trusted-cert -d -r trustRoot -p codeSign`（**需 admin**，放 do shell script 里）。
- `resign_app()`：`codesign --force --deep --sign "haochen Local Signing" --keychain <keychain> <已安装 app>`（DR 固定）。**注意正在运行 app**：重签在下次启动生效，或 App 引导退出后由脚本重签再重开。
- 状态机：未签名→生成身份→授信→重签→待重启→完成；各步结果反馈给 UI（进度/成功/失败原因，如密码错误取消）。

### 2) UI（设置面板或首启引导卡片）
- 首启/设置里当检测到「当前 app 是 ad-hoc 签名（不稳定）」时，显示「**一键修复签名权限**」按钮（替代/补充手动的方案 B 指引）。
- 点击 → 走上述流程（进度条 + 密码框）→ 完成后提示「**请退出并重新打开 haochen**」（重签生效）→ 重启后自动弹权限引导（辅助功能+屏幕录制）→ 授权一次即持久。
- 若检测到已是稳定签名 → 按钮隐藏/显示「已用稳定签名（授权持久）」。

### 3) 授权联动
- 重签完成 + 重启后，`ensure_permissions`（组合引导：辅助功能+屏幕录制）照常引导授权**一次**，此后因 DR 稳定不需要再授权。

## 四、工作量

| 步骤 | 内容 | 预估 |
|---|---|---|
| P8.1 | `app_signing_repair.py`：admin 封装 + 生成身份 + 授信 + 重签 + 状态机 | 1–1.5 天 |
| P8.2 | UI 按钮 + 进度/结果 + 重启引导（首启卡片/设置入口） | 0.5–1 天 |
| P8.3 | 授权联动 + 稳定签名检测（隐藏按钮/显示状态） | 0.5 天 |
| P8.4 | 真机验证（用户输一次密码 + 重签 + 重启 + 授权持久 + 重建不再授权） | 1 天（需用户配合） |
| **合计** | | **约 3–3.5 天** |

## 五、风险与对策

| 风险 | 对策 |
|---|---|
| 密码框不可避免（macOS 安全机制） | 已说明；封装为 App 内一次系统密码框，非终端命令 |
| 正在运行的 app 重签 | 重签后**引导重启**（下次启动加载新 DR）；或 App 退出→脚本重签→重开（更顺，多 0.5 天） |
| openssl 路径 | 用系统 `/usr/bin/openssl`（LibreSSL 3.3.6，合法）；避免依赖 homebrew path |
| 身份/钥匙串密码管理 | App 生成并安全存（`packaging/signing.keychain-pw` chmod 600，或 macOS Keychain）；用户无需记 |
| 生产 mac 无证书工具链 | openssl/security/codesign 系统自带 ✓（已验证） |
| 重签对 /Applications 写入 | 用 admin 的 do shell script 跑整个「生成+授信+重签」，权限最稳 |

## 六、请产品确认

1. 按钮位置：**首启引导卡片**（最醒目）还是**设置面板**？建议两者都放（首启引导 + 设置常驻）。
2. 重签后交互：**提示手动重启**（简单）还是 **App 自动退出→重签→重开**（顺滑，+0.5 天）？
3. 确认后我实施 P8，交付附真机密码框/重签/授权持久验证。

---

## 七、实施完成（2026-08-26）

**落实产品决策**：① 按钮=首启引导卡片 + 设置面板（都放）；② 重签后=自动退出→重签→重开（顺滑版）。

**实现**：
- 后端 `app_signing_repair.py`：`is_stable_signed/app_bundle/identity_exists/_generate_identity/_trust_cert_admin/schedule_relaunch_and_resign/repair_signing`。
  - 生成稳定身份（openssl+security 专用钥匙串，密码 App 生成存 HAOCHEN_HOME/signing chmod600）→ `security add-trusted-cert`（**admin，osascript 弹系统密码框**）→ `codesign --force --deep --sign <稳定身份> --keychain <kc> <app>`（DR 固定）→ detached 后台脚本 `sleep; codesign; open`（App 退出后重签重开，start_new_session 脱离 App）。
- UI：首启 `_first_run_guide`（ad-hoc → 一键修复卡片）+ 设置面板 `_signature_card`（状态 + 一键按钮）；`app_shell.fix_signing` 统一调度，成功后让 App 退出重开。
- 稳定签名检测：`codesign -dvv` 判 `Signature=adhoc` vs `haochen Local Signing`——已稳定则按钮隐藏/显示「已用稳定签名」。

**验证**：
- 身份生成：临时钥匙串测通，证书 EKU=codeSigning、身份导入键链 ✓；`identity_exists` 在未授信前为 False（自签未受信，需 add-trusted-cert——由 admin 步完成，符合预期）。
- 冻结 App 首启实测：ad-hoc → 弹「一键修复签名权限」卡片（AX 取证：说明文字 + 按钮 + 引导文案）✓。
- 回归：`tests/run_all_regressions.sh --real` → **PASS=7 FAIL=0**。

**待用户点一键（admin 密码框）后才能完成端到端**：生成身份→授信→重签→自动重开→授权一次即持久。这是 macOS 安全机制，密码框不可避免（产品已认可）；此后重建/升级同 DR、授权持久。

—— kimi（研发）
