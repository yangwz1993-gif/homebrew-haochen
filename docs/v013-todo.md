# v0.1.3 修复 TODO（研发请在重启会话后处理）

> 来源：无人值守/真机发现 ｜ 2026-08-26

## 1. 权限检测 bug（已根治，v0.1.3）
- **现象**：v0.1.2（stable 签名）启动，系统设置辅助功能里 haochen 开关**已开（蓝）**，但 App 弹窗仍说"还没检测到授权"；App 日志 `accessibility_granted -> False` 多次、`permissions ax=False sr=False`。
- **确诊（2026-08-26 真机实验）**：检测代码本身是对的——**TCC 记录是"假蓝"**。签名身份证书曾重建（旧 cert 01:08 前 → 新 cert leaf 87e9…），旧授权记录里的 designated requirement 与当前二进制对不上，tccd 实际判定未授权（系统甚至重新弹授权框）。实测：**重拨开关不会更新旧记录**（off→on 后全新启动仍 False）；`tccutil reset Accessibility com.haochen.app` 清掉旧记录后重新授权 → 立即 `accessibility_granted -> True`。
- **`reader --check` rc=0 为何与 App 不一致**：reader 是 ad-hoc 签名，且从脚本/终端跑时 TCC 归到**调用方的责任进程**（终端等已授权），它测的是调用方的权限，从来不是 haochen 的——该验证项无效。
- **修复**（`app/haochen_app/permissions.py`）：新增 `reset_tcc_records()`（`tccutil reset` 本 app 的 Accessibility+ScreenCapture，无需管理员）+ `_reset_and_reprompt()`；两个引导弹窗加「开关已开仍无效？重置重授」按钮，失败文案改为指向旧记录残留。系统真授权时 `accessibility_granted()` 返回 True（已验证），无需改检测本身。

## 2. 无人值守验证补充
- 无人值守验证脚本 `/tmp/haochen-unattended-verify.py` 目前只查 reader --check / screenshot / 签名 / 授信失败，**漏了 app 自报的 ax/sr 权限状态**。应把"App 权限日志 ax=False"也列为问题项，避免我这边误报"全部通过"。研发可顺手补一条（非核心）。

## 3. 窗口/气泡无法拖动位置（体验点）
- **现象**：haochen 对话窗口/气泡（frameless 无边框）**拖不动**，用户无法调整位置。
- **要做**：给无边框窗口实现**拖动**（按住标题栏/顶部区域 或 指定区域可用鼠标移动窗口），让用户能自由摆放窗口/桌宠气泡。
