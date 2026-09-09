# haochen RPC 接口契约（App 壳 ↔ 引擎）

> 版本：v2.0（冻结）｜ 日期：2026-09-08 ｜ 作者：研发 Agent-A（P1）
> 适用：P3 三 UI 并行开发的**唯一接口依据**。引擎 = `engine/haochen-engine`（pi v0.84.3 Bun 单文件，`--mode rpc`）。
> 本契约中所有事件名/字段均以真引擎实际 dump 验证（证据：`mock-engine/verification/real-engine-dump.jsonl`、`real-engine-dump-tool.jsonl`）或 pi-source 源码类型（`packages/coding-agent/src/modes/rpc/rpc-types.ts`、`packages/agent/src/types.ts`、`packages/ai/src/types.ts`）为准。

---

## 0. 变更规则（冻结条款）

1. 本文件冻结后，任何改动必须：在 §0.1 变更记录中新增一行（版本号 + 日期 + 改动点 + 影响面），并在多人开发通信中显式通知三个 UI agent（C/D/E）与集成负责人。
2. **只允许追加，不允许改语义**：新增事件字段、新增命令算 minor；改字段名、事件序列或结果协议标记算 major，需引擎、壳、UI 与验证同步改。
3. 契约与真引擎行为冲突时，以真引擎实测为准，当场修文档并走第 1 条流程。

### 0.1 变更记录

| 版本 | 日期 | 改动 | 影响面 |
|---|---|---|---|
| v1.0 | 2026-08-25 | 初始冻结 | — |
| v1.0.1 | 2026-08-25 | 补注：TS 扩展加载实测通过（§1.1），§9 风险 ① 排除。无协议语义变化 | 无（仅状态更新） |
| v1.1 | 2026-08-25 | §1.1 冻结数据隔离约定：新增 `PI_CODING_AGENT_DIR` env、模型配置三文件指向 `config/README.md`；运行期换模型命令 `get_available_models`/`set_model` 的说明见 `config/README.md` §6。消息 schema 无变化 | 壳 spawn 参数；配置前端（Agent-D） |
| v1.2 | 2026-08-30 | 壳侧请求统一 5s 超时；停止/崩溃结算全部 pending；坏 JSON/stderr 仅记录长度与哈希；停止改为后台进程组回收 | EngineClient / supervisor / UI RPC callback |
| v1.3 | 2026-08-30 | 当前会话和双入口 FIFO 队列统一由 SessionCoordinator 持久化；用户消息到 `message_end(role=user)` 才确认出队；崩溃后需用户重发或取消 | supervisor / chat / pet / recovery UI |
| v2.0 | 2026-09-08 | `answer → summary` 两回合改为单回合 `brief + detail`；保留旧标记历史解析，不再发送 summary 踢令 | 扩展 / ConversationController / chat / pet / mock / 验证 |

---

## 1. 传输与生命周期

### 1.1 进程启动（spawn 参数）

```
haochen-engine --mode rpc --no-extensions -e <haochen-ext.ts> \
               --session-dir <APP_SUPPORT>/pi-sessions
```

- **cwd**：`<APP_SUPPORT>/pi-home/`（haochen 专属 home，项目级 `.pi` 发现落在自己目录，与全局 pi 隔离）。
- **环境变量**：
  - `HAOCHEN_PET=1`（扩展据此判定处于宠物进程，强制单回合分层结果协议）；
  - `PI_CODING_AGENT_DIR=<APP_SUPPORT>/agent`（pi 原生 env，P2 起冻结）：引擎的 auth/models/settings/会话默认目录全部重定向到 haochen 数据目录，**运行期间 ~/.pi 零写入**（已实测，见 `engine/RESULT.md` P2 节）。
- `--no-extensions` 与 `-e` 同用：禁用全局/项目扩展自动发现，**仅**显式加载 haochen 专属扩展（提供 `read_screen` 工具 + 分层结果规则注入）。**TS 扩展直接加载已于 2026-08-25 实测通过**（jiti + virtualModules，见 `engine/RESULT.md` 追记），无需预编译 JS。
- 模型/provider/key：由 `<APP_SUPPORT>/agent/` 下 `auth.json` + `models.json` + `settings.json` 提供，schema 与模板见 `config/README.md`（P2 冻结，P3 配置前端的依据）。运行期热切换用 `set_model`；新增 provider 需重启引擎。
- 数据目录约定：`APP_SUPPORT = ~/Library/Application Support/haochen`（开发期用 `HAOCHEN_HOME` 环境变量指到临时目录，壳把 `$HAOCHEN_HOME/agent` 赋给 `PI_CODING_AGENT_DIR`，见总纲 §四.5）。

### 1.2 传输格式

- **stdin**：壳 → 引擎，命令，**每行一个 JSON 对象**（JSONL）。
- **stdout**：引擎 → 壳，响应 + 事件，每行一个 JSON 对象。stdout 上**只**有 JSON；非 JSON 行视为异常（可记录并忽略）。
- **stderr**：引擎日志/报错，壳应捕获落盘用于排障，不参与协议。
- 每条命令可带 `id`（字符串，壳生成，建议 8 位随机 hex），引擎响应原样带回。**壳必须按 `id` 关联响应，不得假设响应到达顺序**（实测：异步命令的响应可能乱序返回，如 `new_session` 慢于其后发出的非法命令报错）。

### 1.3 心跳与崩溃检测

- 协议**无内建心跳**。约定：
  - 壳每 30s 发一次 `get_state` 探活（开销极小）；所有 RPC 请求统一 5s 超时，探针超时视为引擎卡死并触发自动重启。
  - **崩溃检测**：引擎进程退出 / stdout EOF 即崩溃。壳必须感知并：提示用户 + 自动重启引擎（重启后当前会话可用 `switch_session` 恢复到崩溃前会话文件），不得白屏（interaction-spec §9.2）。
- **正常关闭**：壳在后台关闭 stdin 并向独立进程组发 SIGTERM；超时后 SIGKILL 整个进程组。Qt 主线程不得调用同步 `wait()`，解释器退出前由非 daemon reaper 保证无孤儿进程。
- **异常输出**：stdout 非 JSON、非对象或 EOF 半行会发脱敏协议错误；stderr 只落长度与 SHA-256 摘要的 0600 轮转日志，不记录原始正文。引擎停止或退出时，所有未完成请求会收到 `success:false` 及 `errorCode`（`timeout` / `engine_stopped` / `engine_exited`）。

---

## 2. 命令（壳 → 引擎）

### 2.1 prompt — 发送用户消息

```json
{"id":"p1","type":"prompt","message":"帮我看看这个页面讲的是什么"}
```

- `streamingBehavior`：可选 `"steer" | "followUp"`。**约定：引擎忙（busy）时壳发 prompt 必须带 `"streamingBehavior":"followUp"`**（沿用上一版 bridge_rpc.py 行为）；空闲时不带。
- 响应（异步命令，响应只代表「受理成功」，回合事件随后流出）：

```json
{"id":"p1","type":"response","command":"prompt","success":true}
```

### 2.2 abort — 打断当前生成

```json
{"id":"a1","type":"abort"}
```
响应：`{"id":"a1","type":"response","command":"abort","success":true}`。打断后当前回合以 `stopReason:"aborted"` 收尾，已产出内容保留。

### 2.3 new_session — 新建会话（引擎实现）

```json
{"id":"n1","type":"new_session"}
```
响应（实测）：

```json
{"id":"n1","type":"response","command":"new_session","success":true,"data":{"cancelled":false}}
```

### 2.4 switch_session — 切换会话（引擎实现）

```json
{"id":"s2","type":"switch_session","sessionPath":"/Users/x/Library/Application Support/haochen/pi-sessions/2026-08-25T06-00-00-000Z_xxxx.jsonl"}
```
响应：`{"id":"s2","type":"response","command":"switch_session","success":true,"data":{"cancelled":false}}`

### 2.5 get_messages — 拉取当前会话历史（引擎实现）

```json
{"id":"m1","type":"get_messages"}
```
响应：`data.messages` 为消息数组（UserMessage / AssistantMessage / ToolResultMessage，schema 见 §3.2）。**用途**：切换会话 / 重启恢复后渲染历史；气泡与窗口共享会话靠它拉同一份数据。

### 2.6 get_state — 探活 + 状态（引擎实现）

```json
{"id":"s1","type":"get_state"}
```
响应（实测截选）：

```json
{"id":"s1","type":"response","command":"get_state","success":true,"data":{
  "model":{"id":"deepseek-v4-flash-vision-exp","provider":"deepseek","reasoning":true,"..." : "..."},
  "thinkingLevel":"high","isStreaming":false,"isCompacting":false,
  "sessionFile":".../pi-sessions/xxxx.jsonl","sessionId":"...",
  "messageCount":0,"pendingMessageCount":0,"autoCompactionEnabled":true,
  "steeringMode":"one-at-a-time","followUpMode":"one-at-a-time"}}
```

### 2.7 set_session_name — 会话重命名（引擎实现）

```json
{"id":"r1","type":"set_session_name","name":"周末电影计划"}
```

### 2.8 会话列表 / 删除 —【壳侧实现，引擎 RPC 不支持】

- **list**：RPC 无 list 命令。壳扫描 `--session-dir` 下 `*.jsonl`，逐文件读首行 `{"type":"session","id","timestamp"}` 与第一条 user 消息做预览（参考 `previous-version/haochen-app/bridge_rpc.py:list_sessions`），按时间倒序。
- **delete**：RPC 无 delete 命令。壳仅把会话根目录的直接 `.jsonl` 子文件移入私有回收站；**删除当前会话前必须先 `new_session` 并确认状态已切走**。
- 当前会话路径由唯一 SessionCoordinator 在成功切换/状态确认后原子写入 0600 runtime state；失败切换不得覆盖旧路径。supervisor 重启只恢复该权威路径。
- 大窗口与气泡的用户输入进入同一 FIFO；RPC `success` 只代表接受，必须等 `message_end(role=user)` 才可从持久队列移除。崩溃时未确认项标记为待处理，不自动重发，用户可明确取消或重发。

### 2.9 extension_ui_response — 回答扩展 UI 请求（壳 → 引擎）

见 §5 读屏确认。

---

## 3. 流式事件（引擎 → 壳）

### 3.1 一轮对话的事件序列

```
response(prompt, success)          ← 受理回执（实测在 agent_start 之前到达，但不保证，按 id 关联）
agent_start
  turn_start
    message_start  (role=user)     ← 用户消息回显
    message_end    (role=user)
    message_start  (role=assistant, content=[], stopReason="pending")
    message_update × N             ← 流式增量（thinking/text/toolcall，见 §3.3）
    message_end    (role=assistant, 完整权威消息)
    [tool_execution_start          ← 如有工具调用
     tool_execution_update × N
     tool_execution_end
     message_start  (role=toolResult)
     message_end    (role=toolResult)
     turn_end …再进下一 turn…]
  turn_end (message=assistant, toolResults=[])
agent_end   {messages:[...], willRetry:false}
agent_settled                       ← 见 §3.5 时序坑
```

### 3.2 消息 schema（实测 dump）

**UserMessage**（content 恒为数组形态，即使 prompt 发的是纯字符串）：

```json
{"role":"user","content":[{"type":"text","text":"Reply with exactly one word: pong"}],"timestamp":1787641472199}
```

**AssistantMessage**（`message_start` 时 `content:[]`、`stopReason:"pending"`；`message_end`/`turn_end`/`agent_end.messages` 里是完整权威版本）：

```json
{"role":"assistant",
 "content":[
   {"type":"thinking","thinking":"The user wants...","thinkingSignature":"reasoning_content"},
   {"type":"text","text":"pong"}],
 "api":"openai-completions","provider":"deepseek","model":"deepseek-v4-flash-vision-exp",
 "usage":{"input":1234,"output":17,"cacheRead":384,"cacheWrite":0,"reasoning":14,
          "totalTokens":1635,
          "cost":{"input":0.00017276,"output":0.00000476,"cacheRead":0.0000010752,"cacheWrite":0,"total":0.00017859}},
 "stopReason":"stop","timestamp":1787641472204,
 "responseId":"901fd8cc-...","rawStopReason":"stop"}
```

**ToolResultMessage**：

```json
{"role":"toolResult","toolCallId":"call_00_beJgExJB5enL1nZWbfOg6500","toolName":"bash",
 "content":[{"type":"text","text":"hello-from-tool\n"}],"isError":false,"timestamp":1787641502216}
```

`stopReason` 取值：`"stop" | "length" | "toolUse" | "error" | "aborted"`（流式中为 `"pending"`）。

### 3.3 message_update — 流式增量（**UI 打字机就靠它**）

线上形状：`{"type":"message_update","usage":{...},"assistantMessageEvent":{...}}`。
注意：**线上事件里 `assistantMessageEvent` 已剥离 `partial` 字段**（引擎侧 `toJsonEvent` 移除累积快照）；UI 用增量自己拼，不要找 `partial`。

`assistantMessageEvent.type` 全集：

| type | 关键字段 | 说明（实测） |
|---|---|---|
| `thinking_start` | `contentIndex` | 思考块开始 |
| `thinking_delta` | `contentIndex`, `delta` | 思考增量文本 |
| `thinking_end` | `contentIndex`, `content` | 思考块完整文本 |
| `text_start` | `contentIndex` | 正文块开始 |
| `text_delta` | `contentIndex`, `delta` | 正文增量（打字机追加它） |
| `text_end` | `contentIndex`, `content` | 正文块完整文本 |
| `toolcall_start` | `contentIndex`, `id`, `toolName` | 工具调用开始（`id`/`toolName` 由引擎补到事件上） |
| `toolcall_delta` | `contentIndex`, `delta` | 工具参数 JSON 的增量片段 |
| `toolcall_end` | `contentIndex`, `toolCall` | 完整 `{"type":"toolCall","id","name","arguments":{...}}` |

`contentIndex` 是 content 数组下标，用于区分同一条 assistant 消息里的多个块（思考块 0、正文块 1…，实测如此递增）。

实测示例（真引擎 dump）：

```json
{"type":"message_update","usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"assistantMessageEvent":{"type":"text_delta","contentIndex":1,"delta":"p"}}
{"type":"message_update","usage":{"input":0,"...":"..."},"assistantMessageEvent":{"type":"toolcall_start","contentIndex":1,"id":"call_00_beJgExJB5enL1nZWbfOg6500","toolName":"bash"}}
```

### 3.4 工具执行事件（工具卡数据源）

```json
{"type":"tool_execution_start","toolCallId":"call_00_beJgExJB5enL1nZWbfOg6500","toolName":"bash","args":{"command":"echo hello-from-tool"}}
{"type":"tool_execution_update","toolCallId":"call_00_...","toolName":"bash","args":{"command":"echo hello-from-tool"},"partialResult":{"content":[{"type":"text","text":"hello-from-tool\n"}],"details":{}}}
{"type":"tool_execution_end","toolCallId":"call_00_...","toolName":"bash","result":{"content":[{"type":"text","text":"hello-from-tool\n"}]},"isError":false}
```

- 工具卡三态：`tool_execution_start`（运行中）→ `tool_execution_update`（部分输出，可选展示）→ `tool_execution_end`（✓/`isError:true` ✗）。
- `toolCallId` 与 `toolcall_start`/`toolcall_end` 的 `id` 一致，用于把工具卡与消息流里的调用块对上。

### 3.5 回合结束判定时序坑（必须遵守）

- `agent_end` 带 `{messages, willRetry}`；`agent_settled` 是「真正闲下来」信号。
- **坑**：上一轮结束后可能延迟再触发一次 `agent_settled`（引擎内部 settle，实测存在）。驱动方不得以「等到下一个 settle」为回合结束条件。**正确做法：先等到 prompt 的 response，再等其后的第一个 `agent_end`**（rpc-smoke-test.mjs 的 settle 计数递增法亦可）。
- `willRetry:true` 表示引擎将自动重试（auto-retry），UI 不应把该回合当终态。

---

## 4. brief / detail 单回合协议

### 4.1 选定方案：**扩展注入规则 + 一次模型调用生成两层结果**

haochen 扩展在 `before_agent_start` 钩子向 system prompt 注入 RESULT_RULES。工具调用结束后，模型最终正文严格按以下顺序输出：

```
【brief】
<一句结论 + 最多两个高信息量要点，通常 24～120 个中文字符>
【/brief】
【detail】
<完整回答：依据、步骤、限制与产物>
【/detail】
```

桌宠只显示 `brief`；用户点“查看详情”后，完整窗口按“brief 在前、detail 在后”呈现。这样去掉了旧版为生成 summary 而产生的第二次模型等待、成本和失败点。

### 4.2 壳侧职责（冻结逻辑）

1. 一轮 `agent_end` 后，从最后一条 assistant 文本分别解析配对 `brief` 与 `detail`。
2. 两块齐全：一次性构造 `TurnResult(brief, detail)`，并结束 busy 状态；不得再发内部 prompt。
3. 降级顺序：
   - 新协议缺块时，兼容解析旧 `【summary】` 为 brief、`【answer】` 为 detail；
   - detail 仍缺失时，剥离所有协议标记后使用原始正文；
   - brief 仍缺失时，从 detail 做确定性抽取式短结；
   - 任一降级都必须终结当前流程，禁止递归请求模型。
4. 旧前缀 `haochen-summary-phase` 只用于过滤既有会话历史，不再由壳发送，扩展也不再响应。

### 4.3 标记汇总（冻结字符串）

| 标记 | 位置 | 作用 |
|---|---|---|
| `【brief】…【/brief】` | 模型最终正文第一块 | 桌宠临时简答与详情页首屏结论 |
| `【detail】…【/detail】` | 模型最终正文第二块 | 展开后的完整回答 |
| `【answer】` / `【summary】` | 旧会话历史 | 仅兼容读取，不再生成 |
| `haochen-summary-phase` | 旧会话历史 | 仅过滤，不再发送 |
| `HAOCHEN_PET=1` | 环境变量 | 扩展判定 App 进程、强制分层结果协议 |

---

## 5. 读屏确认（敏感操作授权）

### 5.1 机制

`read_screen` 工具（haochen 扩展注册）执行前调 `ctx.ui.confirm()`，引擎在 RPC 模式下转成 `extension_ui_request` 事件发给壳，**工具执行阻塞等壳答复**（超时 120s 按拒绝处理）。

### 5.2 事件（引擎 → 壳）

```json
{"type":"extension_ui_request","id":"<uuid>","method":"confirm",
 "title":"haochen 想读屏",
 "message":"需要读取当前锁定窗口的屏幕内容才能回答。点「读吧」或在输入框回 y 允许；回 n 拒绝。",
 "timeout":120000}
```

### 5.3 响应（壳 → 引擎）

```json
{"type":"extension_ui_response","id":"<同一个 uuid>","confirmed":true}
```

- `confirmed:true` = 用户点「读吧」；`confirmed:false` = 「不读」；`{"id":..., "cancelled":true}` = 用户 Esc/收起气泡取消。
- 拒绝后工具返回 `isError:true` 的 ToolResult（`tool_execution_end.isError=true`），模型据此改答「没读屏，以下基于已有信息」。
- 其它 `extension_ui_request.method`（`select`/`input`/`editor`/`notify`/`setStatus`/`setWidget`/`setTitle`/`set_editor_text`）：haochen 扩展当前只用 `confirm`；壳对未实现的 `select`/`input`/`editor` 一律回 `{"type":"extension_ui_response","id":...,"cancelled":true}`，对 `notify`/`setStatus` 等 fire-and-forget 可忽略（沿用上一版 bridge_rpc.py 策略）。

### 5.4 感知提示（壳侧，interaction-spec §4.1）

壳在收到 `tool_execution_start` 且 `toolName=="read_screen"`（或紧随其前的 confirm 请求）时，渲染感知提示「我正看一下你的屏幕…」+ 桌宠姿态切换。确认条按钮文案固定「读吧 / 不读」。

---

## 6. 错误路径

| 场景 | 线上表现 | 壳的处理 |
|---|---|---|
| 命令非法/失败 | `{"id","type":"response","command":"<cmd>","success":false,"error":"..."}`（实测：`Unknown command: bogus_command`） | 按 id 落到发起方，UI 报错条 |
| 模型/API 错误 | assistant `message_end` 带 `stopReason:"error"` + `errorMessage`；随后 `agent_end`（`willRetry` 可能 true，引擎自动重试） | 显示错误块 + 重试入口；`willRetry:true` 时显示「重试中」 |
| 用户打断 | assistant `stopReason:"aborted"`，事件序列照常收尾到 `agent_end` | 保留已产内容，标「已停止」 |
| 扩展错误 | `{"type":"extension_error","extensionPath","event","error"}` | 日志 + 用户可读提示 |
| 引擎崩溃/被杀 | 进程退出、stdout EOF | §1.3：感知 + 重启 + 恢复会话，不白屏 |

---

## 7. 引擎实现 vs 壳侧实现（速查）

| 能力 | 实现方 | 依据 |
|---|---|---|
| prompt / 流式事件 / abort | 引擎 | pi RPC 原生 |
| brief + detail 单回合结果 | **协作**：扩展注入规则，壳解析与确定性兜底 | §4 |
| 读屏确认 | 引擎（扩展 confirm → extension_ui_request），壳负责渲染确认条并回响应 | §5 |
| new_session / switch_session / get_messages / get_state / set_session_name | 引擎 | §2，均实测 |
| **会话列表 list** | **壳侧**（扫 session-dir jsonl） | §2.8，RPC 无此命令 |
| **会话删除 delete** | **壳侧**（删 jsonl 文件） | §2.8，RPC 无此命令 |
| 心跳/崩溃检测/重启 | 壳侧 | §1.3，协议无心跳 |
| 页面变化检测（读屏签名侧车文件） | 壳侧 + 扩展写签名 | 沿用上一版 last-read-sig.json |
| 气泡↔窗口同一会话 | 壳侧（共享 get_messages 数据源） | interaction-spec §7 |

---

## 8. 验证证据索引

- `mock-engine/verification/real-engine-dump.jsonl` — 真引擎普通对话全事件（30 行，2026-08-25，DeepSeek 廉价 prompt）。
- `mock-engine/verification/real-engine-dump-tool.jsonl` — 真引擎 bash 工具调用全事件（73 行，含 toolcall_*/tool_execution_*）。
- `mock-engine/driver.py` — L1 驱动脚本，对 mock 与真引擎跑同一套断言（总纲 §四.2 P1 门禁）。
- pi-source 类型源头：`packages/coding-agent/src/modes/rpc/rpc-types.ts`（命令/响应/extension_ui）、`src/modes/json-event.ts`（message_update 线上剥形）、`packages/agent/src/types.ts`（AgentEvent）、`packages/ai/src/types.ts`（Message/Usage/AssistantMessageEvent）。

## 9. 已知风险（冻结时记录）

1. ~~**Bun 二进制加载扩展未验证**~~ → **已排除（2026-08-25 实测）**：jiti + `virtualModules`（typebox/pi 包静态内嵌）在编译二进制上可直接加载 TS 扩展；上一版 `ext/index.ts` 原样加载成功，confirm 链路与当时的结果规则注入端到端验证通过。证据：`engine/RESULT.md` 追记 + `mock-engine/verification/ext-test-*`。
2. `export_html` 等依赖二进制旁资源的功能不可用（P0 已知限制），本契约不依赖它们。
3. `agent_settled` 时序坑（§3.5）已在上一版咬过人，驱动层必须照 §3.5 实现。
