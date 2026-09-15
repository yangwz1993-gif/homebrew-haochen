# haochen Constitution

> 原则给人看，规则给门禁跑。每条规则必须能被脚本检查。

## 原则
1. 用户视角优先：任何改动先问「用户能不能感知到好」
2. 验证才算完成：红-绿-变异三段存证，门禁全绿
3. 架构可演进，变更走 ADR 收费站（docs/adr/）

## 可执行规则（quality.sh / make check）
- R1 测试 ratchet：用例数/测试文件/skip/覆盖率只增不减（已实现：scripts/check_test_integrity.py + tests/baseline.json，挂入 make check）
- R2 每个修复必须带 evidence/ 存证（红→绿→变异）
- R3 提醒语义：已读不提醒、提醒指名应用、不抢键盘焦点（测试：tests/test_attention*.py 等）
- R4 占位卡永不假装未读（样式弱化，不可点）
