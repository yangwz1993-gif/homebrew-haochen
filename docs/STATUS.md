# STATUS — haochen（从这里开始）

> 更新于：2026-09-16 11:05 ｜ 瞬时状态带「截至+复核方式」，以复核为准

## 0. 一句话现状
0.6.2-beta.3 代码侧全部修完（A 区 7 项 + B 区 23 项，head=9b7a7e9），dmg 已构建；**卡在用户真机冒烟总验收**（清单 evidence/真机冒烟清单_0.6.2-beta.3.md，需用户本人逐项签字），验收后才谈推送/分发。

## 1. 进度
- [x] A 区 7 项用户视角问题（Hi 空占位/动态抖动/Otty 分发/模型切换锁死/刘海对齐 MioIsland/补丁债/git 慢）
- [x] B 区 23 项冒烟新发现（含 C-11 二维状态、B-9/B-10、未回私聊两步法 9b7a7e9）
- [x] 质量机制：测试 ratchet（挂 make check）+ 红绿变异存证 + AppKit 类探针 + 真机冒烟清单
- [ ] **用户真机冒烟签字**（唯一阻塞项）
- [ ] 授权后：git push / Release / cask 分发

## 2. 已确认口径与决策
- 版本产物只放 ver/0.6.2-beta.3，只要应用代码+包+最新 readme（用户确认于 09-14 14:03）→ 影响：不混杂物
- v4 准出=新行为+不变量+红绿变异存证（确认于 09-14 15:07）→ 影响：每个修复必带测试存证
- 施工不分期、本次全搞定（确认于 09-14 15:33）→ 影响：计划禁止写「后续几期」
- 修复范围 b-9/b-10/c-11，a 部分待本轮完（确认于 09-15 06:17）→ 影响：a 部分禁动
- 提醒语义：已读不提醒、提醒指名应用、连接问题在连接卡片不在刘海（确认于治理过程，commit 9c21b19）→ 影响：改提醒逻辑先对齐此口径
- 未回私聊=推断式标注，两步法全量验证群/私聊（9b7a7e9）→ 影响：不谎称「精确未读」

## 3. 关键事实速查
- 仓库：本目录，分支 feat/v0.5-dashboard@7717b1b（截至 09-16 冷启动复核：`git rev-parse --short HEAD`；9b7a7e9 之上多了 docs 提交）→ 影响：改动都在此提交
- 包：packaging/dist/haochen-0.6.2-beta.3.dmg（已构建）→ 影响：分发放大前先过冒烟
- 治理总表：~/Desktop/doc/haochen问题治理总表_20260915_v2.md（v1 作废）→ 影响：bug 编号以此为准
- 冒烟清单：evidence/真机冒烟清单_0.6.2-beta.3.md → 影响：验收入口
- 基线：tests/baseline.json（815 用例/80.46% 覆盖率）→ 影响：只增不减
- 签名口令目录：~/Library/Application Support/haochen/signing/（勿删）
- hi CLI 事实：无未读/会话类型接口；机器人 senderId 尾缀 @bot.com；跳转用 citylink://client/chat/openConversation?type=chat&id=<chatId>（**不带 host**）

## 4. 起步动作
1. 校准：`git log --oneline -3 && git status`（与上文不一致以实际为准并回写本节）
2. 环境前置（见 AGENTS.md 快速上手）+ `bash -c 'make check'` 确认基线绿
3. 若用户已完成冒烟签字 → 收尾（CHANGELOG/推送授权确认）；否则等签字，可先做 C 区长期项的调研
4. 验收：`make check` 全绿 + 每项修复有 evidence/ 存证
