# P3 交付：M-C 对话前端 + M-E 桌宠（研发 → 产品）

> 日期：2026-08-25 ｜ 交付方：kimi（研发）｜ 审查依据：`docs/product-acceptance.md` §四
> 状态：**请求产品符合性审查**。M-D（配置）此前已验收，本次交付后 P3 三 UI 全部齐活。

---

## 一、交付范围与代码路径

| 模块 | 代码 | 独立入口 | 验证驱动 |
|---|---|---|---|
| M-C 对话前端 | `app/haochen_app/chat/`（window 541 / widgets 415 / sidebar 130 / theme 112 行） | `app/run_chat.py` | `app/haochen_app/chat/verification/run_scenarios.py` |
| M-E 桌宠 | `app/haochen_app/pet/`（app 318 / bubble 483 / pet_window 191 / hotkey 52 / state 44 行） | `app/run_pet.py` | `app/haochen_app/pet/verification/run_scenarios.py` |
| 共享层 | `app/haochen_app/engine_client.py`（236 行，RPC/断线感知）、`conversation.py`（204 行，两步 answer/summary 状态机） | — | 随两模块覆盖 |

三 UI 共享文件（`config/` schema、`docs/rpc-contract.md`）只读未改，零冲突。

## 二、自测结果（2026-08-25 17:32 研发独立复跑，非子 agent 旧数据）

| 套件 | 命令 | 结果 |
|---|---|---|
| M-C 场景 | `HAOCHEN_MOCK=1 MOCK_TICK_MS=5 QT_QPA_PLATFORM=offscreen app/.venv/bin/python app/haochen_app/chat/verification/run_scenarios.py` | **5/5 场景全过**：普通对话（打字机+结论先行+Markdown）/ 读屏确认（确认条→授权→工具卡✓）/ 错误路径（错误条+重试）/ 多会话切换（历史渲染）/ 引擎崩溃→感知→重启恢复 |
| M-E 场景 | `MOCK_TICK_MS=20 QT_QPA_PLATFORM=offscreen .venv/bin/python haochen_app/pet/verification/run_scenarios.py` | **17/17 断言全过**（S1 普通问答姿态 idle→thinking→idle、S2 读屏确认「读吧/不读」、S3 错误 alert(angry)、S4 Esc 打断不卡死、S5 Esc 收起回 IDLE） |
| L1 回归 | `cd mock-engine && python3 driver.py` | **45/45 通过**（契约未被破坏） |
| M-D 回归 | `python -m haochen_app.settings.selfcheck` | **26/26 通过** |

## 三、截图证据（视觉走查用）

- M-C：`app/haochen_app/chat/verification/01~08-*.png`（8 张：正常对话/读屏确认/授权后/工具卡展开/错误重试/多会话/崩溃/重启）
- M-E：`app/haochen_app/pet/verification/01~08-*.png`（12 张：idle/唤起/思考/短结/读屏确认/错误/打断/收起）
- 研发目测：米白 `#fdf6e3`、深描边 `#2b2b22`、圆角、气泡右下尾巴、绿色强调色均符合 `docs/visual-spec.md`；**最终美观判定请产品/用户终审**。

## 四、功能对照验收清单（product-acceptance.md §一）

- **#1 对话可用**：流式打字机 ✅、Markdown ✅、工具卡可见可展开 ✅、会话新建/切换/删除/搜索 ✅、历史持久化（jsonl，重启可恢复）✅
- **#2 桌宠可用**：像素小哥常驻 ✅、双击/⌃⌥P 唤起 ✅、读屏确认「读吧/不读」✅、answer→summary 两步（气泡短结+详答）✅、Esc/点空白收起 ✅
- **交互规范**：感知可见（"我正看一下你的屏幕…"+thinking 姿态）✅、结论先行（L1 短结）✅、先问再动（确认条）✅、随时可打断（Esc abort 已验证不卡死）✅、失焦不自动收起（changeEvent 保证）✅、动态对话流（逐条弹出 MVP 动效）✅

## 五、已知限制（不隐瞒）

1. **全程 mock 引擎**：M-C/M-E 按契约对 mock 开发；接真引擎全链路是 P4 内容，本次不含。
2. **气泡↔对话窗口会话共享**：两入口目前各自独立进程/会话，共享同一会话是 P4 集成验收项（契约 v1.1 的 get_state/switch_session 已具备能力）。
3. **全局热键 ⌃⌥P**：依赖 pyobjc + 辅助功能权限；无权限时自动降级为双击唤起（run_pet.py 注释已说明）。权限引导属 P4/P5。
4. **录屏**：按总纲 §三.7 要求应附操作录屏；offscreen 自动化截图已全量提供，真人录屏建议在 P4 集成后一次性做（更有意义）。
5. offscreen 截图不含 macOS 真实窗口边框/阴影，真机观感以用户终审为准。

## 六、下一步（收到产品审查结论后启动）

P4 集成联调：单一 App 壳 + 引擎进程管理（启停/崩溃重启）+ 三 UI 打通 + **真引擎全链路**（配置 key→对话→桌宠唤起→读屏确认→两步回答）+ 双入口同一会话。预估 3–5 人天，串行。

—— kimi（研发）
