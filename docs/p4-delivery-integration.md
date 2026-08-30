# P4 交付：集成联调（研发 → 产品）

> 日期：2026-08-25 ｜ 交付方：kimi（研发）｜ 审查依据：`docs/product-acceptance.md` §四、开发总纲 §二 P4
> 状态：**请求产品符合性审查**。P4 门禁全项达成（含真引擎全链路）。

---

## 一、P4 门禁对照（总纲原文 → 达成证据）

| 门禁 | 结果 | 证据 |
|---|---|---|
| 单一 App 壳（单一入口） | ✅ | `app/run_app.py` 唯一入口；`app/haochen_app/app_shell.py` 装配三 UI |
| 进程管理：引擎随 App 启停、崩溃重启 | ✅ | `app/haochen_app/supervisor.py`：崩溃感知 → 退避重启（1s/2s/5s，3 次失败明确报错不白屏）→ 会话自动恢复 |
| 模块间配置打通（配置 UI 写的配置被引擎读到） | ✅ | 设置保存 → 同 provider 换模型 `set_model` 热切换；provider/key 变更 → 询问重启引擎；首启模板初始化 + key 只读导入（~/.pi 只读） |
| **真引擎全链路**：配置 key → 对话 → 桌宠唤起 → 读屏确认 → 两步回答 | ✅ | `app/verification/p4_real_chain.py` 真 DeepSeek 16/16（两轮独立运行均全过） |
| **气泡与对话窗口共享同一会话，追问不丢上下文** | ✅ | 唯一 EngineClient 天然同会话（真实 jsonl 断言）；**崩溃重启后仍切回同一 jsonl，跨重启追问上下文完整**（R5 实证） |

## 二、新增/变更文件

| 文件 | 说明 |
|---|---|
| `app/haochen_app/supervisor.py` | 新增 180 行：引擎进程管家（唯一 client、退避重启、会话恢复、双入口 turn 仲裁） |
| `app/haochen_app/app_shell.py` | 新增：三 UI 装配 + 配置生效链 + 首启引导（key 导入经用户同意，~/.pi 只读） |
| `app/run_app.py` | 新增：App 唯一入口 |
| `app/ext/index.ts` | 新增：引擎扩展（read_screen + 两步协议注入），适配自 previous-version（去旧 /pet 命令，路径支持 HAOCHEN_HOME/HAOCHEN_PETREAD 覆盖） |
| `app/haochen_app/engine_client.py` | 改：spawn_argv 注入 `HAOCHEN_PET=1` + `HAOCHEN_HOME`（契约 §1.1 细化，config/README §1 既定约定） |
| `app/haochen_app/pet/app.py` | 改（增量，不破坏 standalone）：client/supervisor 注入、双入口仲裁、重启期间消息暂存自动补发 |
| `app/haochen_app/chat/window.py` | 改（增量）：supervisor 注入、气泡回合镜像、确认只路由发起方、排队跨入口泄流 |
| `mock-engine/mock_engine.py` | **bugfix**：stdin 预读吞行（select+buffered readline 丢流水线命令，P4 双 get_state 并发暴露）→ 自有缓冲 os.read；L1 45/45 回归通过 |
| `app/verification/p4_integration.py` | 新增：mock 集成 25 断言 |
| `app/verification/p4_real_chain.py` | 新增：真引擎全链路 16 断言 + 问答实录 |

## 三、验证结果（2026-08-25 研发复跑）

| 套件 | 结果 |
|---|---|
| **P4 真引擎全链路**（真 DeepSeek） | **16/16 ×2 轮**（第二轮含真读屏成功：「你当前屏幕上显示的是『π – yangwz』这个终端窗口」） |
| P4 mock 集成（单引擎/镜像/确认路由/并发仲裁/崩溃恢复/配置链/失败兜底） | **25/25** |
| L1 引擎协议（mock driver） | 45/45 |
| M-C 场景回归 | 5/5 |
| M-E 场景回归 | 17/17 |
| M-D 自检回归 | 26/26 |

问答实录：`app/verification/p4-real-transcript.md`；截图：`app/verification/p4-*.png`（mock 链路 5 张）+ `p4-real-*.png`（真链路 3 张：对话流/真读屏/崩溃恢复后）。

## 四、关键体验确认（acceptance §二）

- 感知可见：「我正看一下你的屏幕…」+ read_screen 工具卡「✓ 完成」✅（见 p4-real-02 截图）
- 结论先行：L1 短结卡片先于详答 ✅
- 先问再动：读屏确认「读吧/不读」，只弹在发起方入口（双入口不重复打扰）✅
- 不白屏：kill 引擎 → 横幅「自动重启中」→ 1s 内恢复 + 会话还原 ✅
- 隔离：全程只读 ~/.pi（key 导入经同意）；数据全在 HAOCHEN_HOME ✅

## 五、已知限制与风险（如实上报）

1. **真读屏的 TCC 归因**：首轮真链路读屏曾失败一次（引擎二进制无「辅助功能」权限）；同链路二轮成功（读到真实窗口）。根因是 macOS 把 AX 权限归到「责任进程」——生产形态下 .app 是责任进程，**P5 打包必须落实权限引导**（本就在 DoD#5/验收 §0.1）；非代码 bug，但请产品在 P5 验收里重点关注。
2. **读屏执行体**暂复用 `~/.local/bin/haochen`（旧版 petread，shell 脚本调旧 venv python）——本机可用；**P5 必须内嵌独立读取器**（`HAOCHEN_PETREAD` 环境变量已预留指向 Resources）。
3. mock 引擎会话纯内存，崩溃恢复「切回原会话」在 mock 下只能验证壳行为（switch_session 已发出）；端到端恢复由真链路 R5 覆盖（已实证）。
4. 页面变化检测（旧版「页面状态」前缀）未接入新壳——P6 打磨项，不阻塞。
5. 全局热键 ⌃⌥P 需 pyobjc + 辅助功能权限；无权限自动降级双击唤起。

## 六、下一步

P5 打包 dmg：PyInstaller onedir → .app（内嵌引擎二进制 + 读屏执行体 + LSUIElement + 权限文案）→ ad-hoc 签名 → .dmg。预估 4–7 人天。等 P4 过审即开工。

—— kimi（研发）
