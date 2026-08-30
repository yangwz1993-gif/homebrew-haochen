# P4 集成联调 · 设计与施工计划

> 日期：2026-08-25 ｜ 作者：kimi（研发）｜ 依据：开发总纲 §二 P4、契约 v1.1、P3 交付代码现状
> 性质：**门禁内串行任务**，待产品 P3 审查通过后开工。本文先行锁定设计，开工后不再返工。

## 一、P4 目标（总纲原文）

三 UI + 真引擎合成一个 App 壳：单一入口、进程管理（引擎随 App 启停、崩溃重启）；
模块间配置打通（配置 UI 写的配置被引擎读到）。
**验收门禁**：App 内完成「配置 key → 对话 → 桌宠唤起 → 读屏确认 → 两步回答」全链路（真模型）；
**气泡与对话窗口共享同一会话，追问不丢上下文**。

## 二、现状盘点（P3 已具备的能力，复用不重写）

| 能力 | 现状 | P4 处置 |
|---|---|---|
| 引擎 spawn/stdio RPC/崩溃感知 | `engine_client.EngineClient`（契约 §1.1 启动三件套） | 复用，外套 Supervisor |
| 两步协议 answer→summary | `conversation.ConversationController`（turn 归属=谁 send 谁持有 _phase） | 复用，每 UI 一个 ctrl 的模式保留 |
| ChatWindow 依赖注入 | `ChatWindow(client=None)` 已支持共享 client | 直接注入 |
| PetApp 依赖注入 | 目前自建 client（`PetApp(mock=None)`） | **改**：加 `client=` 参数，与 ChatWindow 对齐 |
| 崩溃恢复 | chat 有 banner+手动重试；pet 有自动重启 | **收口**到 Supervisor 统一策略 |
| 配置 schema/读写 | `config/`（P2 冻结）+ settings `config_store` | 复用；补「保存后→引擎生效」的接线 |
| 模型热切换 | `EngineClient.set_model`（同 provider 立即生效） | 接线到设置保存信号 |

## 三、集成架构

```
run_app.py（唯一入口）
  └─ QApplication（setQuitOnLastWindowClosed=False，Accessory 形态）
  └─ EngineSupervisor（新增 app/haochen_app/supervisor.py，~150 行）
       ├─ 拥有唯一 EngineClient（真引擎；HAOCHEN_MOCK=1 时 mock）
       ├─ 启动/停止随 App；崩溃 → 指数退避自动重启（1s→2s→5s→上限 30s）
       ├─ 重启后自动 get_state 恢复当前会话上下文；广播 engine_state 信号
       └─ 心跳：复用 reader 线程 EOF 感知（不新增协议层心跳——契约外不发明）
  ├─ PetApp(client=supervisor.client)         # 常驻桌宠 + 气泡
  ├─ ChatWindow(client=supervisor.client)     # 默认隐藏；气泡「展开」/菜单唤起
  └─ SettingsWindow                            # 独立弹出；保存信号 → supervisor
```

### 关键设计 1：双入口同一会话（验收硬指标）

- 引擎侧天然同会话：两 UI 共用一个 EngineClient = 同一引擎进程 = 同一当前会话 jsonl。
- **turn 归属**：沿用 ConversationController._phase 语义——谁 send 谁的 ctrl 持有本轮；
  另一 ctrl 静默（_phase 空，不响应事件）。已验证安全。
- **镜像**：气泡发起的一轮，若对话窗口处于可见态，窗口在 agent_end 后从
  `get_messages` 重渲染当前会话（不重复流式渲染，避免双 ctrl 抢事件）。
  窗口发起的一轮，气泡保持 idle 不抢显。
- **确认路由**：`extension_ui_request`（读屏确认）只路由到发起 turn 的 UI 表面；
  另一表面不弹。Esc/abort 同理只属发起方。

### 关键设计 2：配置 → 引擎生效链

- SettingsWindow 保存后 emit `config_saved`：
  - 仅模型/思考档变化（同 provider）→ `client.set_model(...)` 热生效（已有能力）；
  - provider/API key 变化 → 提示「重启引擎生效」，用户确认后 Supervisor.restart()；
  - 主题变化 → 三 UI 即时换 stylesheet（纯壳侧）。
- 首启无 key：App 启动自检（settings.selfcheck 已有逻辑）→ 无可用 key 则先弹设置页并
  引导，而不是让对话报错。

### 关键设计 3：进程管理

- App quit → Supervisor.stop()（terminate 3s → kill），引擎不残留孤儿进程。
- 崩溃 → 自动重启 + 恢复会话；连续 3 次失败 → 明确错误页（不白屏，验收 §三）。
- kill 引擎 → UI 横幅「引擎已重启/重启中」（复用 chat 现有 banner，上提到 Supervisor 信号驱动）。

## 四、施工步骤（串行，预估 3–5 人天）

1. `supervisor.py` + 单测（崩溃/重启/退避/恢复会话）— 0.5d
2. `PetApp(client=)` 注入改造 + 回归 M-E 场景脚本 — 0.5d
3. `run_app.py` 单入口 + 三 UI 装配 + 确认路由 — 1d
4. 配置生效链接线（set_model/重启提示/首启引导）— 0.5d
5. **真引擎全链路验收脚本**（L3 清单 §四.4 全过，真模型一轮，花一次钱）— 1d
6. 录屏 + 截图 + 用户终审 — 0.5d
7. 缓冲（集成历来爆雷）— 1d

## 五、验证计划

- L1 回归：`mock-engine/driver.py` 45 断言（每步施工后跑）。
- M-C/M-E/M-D 场景脚本：注入改造后全部复跑（防回归）。
- 新增 `app/verification/p4_integration.py`：offscreen 驱动单入口 App——
  气泡提问 → 窗口可见同会话历史 → 窗口追问 → 气泡可见上下文延续（断言同 session path）；
  kill 引擎 → 自动恢复；配置热切换。
- 真链路：人工一轮（配置 key→对话→桌宠唤起→读屏确认→两步→追问），录屏存证。

## 六、风险

| 风险 | 对策 |
|---|---|
| 双 ctrl 同 client 事件串扰 | turn 归属已验证；集成脚本断言「静默方不渲染」 |
| 引擎重启丢会话上下文 | 重启后 get_state + switch_session 回原 jsonl；脚本断言追问不丢 |
| PyQt 单 App 多窗口生命周期 | QuitOnLastWindowClosed=False；显式 quit 路径（桌宠菜单退出） |
| 真引擎 ext（read_screen/两步注入）加载 | P1 已验证 Bun 直载 TS 扩展；P4 真链路复验 |
