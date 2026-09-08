# mock-engine — haochen 假引擎（P3 UI 联调打桩）

按 `docs/rpc-contract.md` v2.0 吐出**确定性**假事件流的 Python 引擎替身。
事件名、字段、序列与真引擎（`engine/haochen-engine`，pi v0.84.3 `--mode rpc`）一致，
对照 `verification/real-engine-dump*.jsonl` 真机 dump 编写。

无需模型、无需网络、无需 API key，Python 3.9+ 标准库即可运行。

## 文件

| 文件 | 说明 |
|---|---|
| `mock_engine.py` | mock 引擎本体（stdio JSONL，与真引擎同协议） |
| `driver.py` | 驱动 + L1 断言脚本：对 mock 走全流程，对真引擎跑协议级断言 |
| `tools/dump_real_events.py` | 真引擎事件 dump 工具（验证证据用，会真实调一次模型） |
| `verification/` | 真机 dump 与演示录屏（jsonl） |
| `demo.md` | 驱动跑 mock 的完整输出与逐场景解释 |

## 快速开始

```bash
cd mock-engine

# 全流程自检（断言数量会随覆盖增长，以退出码为准）
python3 driver.py

# 手动把玩：另起进程，自己往 stdin 写 JSONL
python3 mock_engine.py
# 然后粘贴（每行一条）：
# {"id":"p1","type":"prompt","message":"你好"}
# {"id":"p2","type":"prompt","message":"看看我的屏幕上有什么"}
#   → 收到 extension_ui_request 后回：
# {"type":"extension_ui_response","id":"<请求里的 id>","confirmed":true}

# 对真引擎跑协议级断言（会调一次廉价模型）
python3 driver.py --engine ../engine/haochen-engine --real
```

环境变量 `MOCK_TICK_MS`（默认 30）：每个流式增量的间隔毫秒。UI 调打字机效果用默认值，
跑自动化测试设 `MOCK_TICK_MS=0` 全速。

## 场景触发（按 prompt 关键词，确定性）

| prompt 包含 | 行为 |
|---|---|
| （任意普通文本） | thinking 流式 + 同回合 `【brief】…【/brief】` 与 `【detail】…【/detail】` |
| `读屏` / `屏幕` / `read_screen` | read_screen 工具调用：toolcall 流式 → `tool_execution_start` → `extension_ui_request`(confirm「读吧/不读」) → 等壳授权 → `tool_execution_end`（授权=isError false，拒绝/取消=isError true + 拒答文案）→ 第二个 turn 出 brief + detail |
| `错误` / `mock-error` | 错误回合：assistant `stopReason:"error"` + `errorMessage`，`agent_end.willRetry=false` |

流式中途发 `{"id":"x","type":"abort"}` 可打断（当前 assistant 消息以 `stopReason:"aborted"` 收尾）。

## 会话命令

`new_session` / `switch_session` / `get_messages` / `get_state` / `set_session_name`
均按契约响应，多会话历史互相隔离（内存态，进程退出即失——**mock 不落盘**）。
契约中「壳侧实现」的会话 list/delete（扫/删 jsonl 文件）不属于引擎行为，mock 不提供。

## 给 UI agent 的接入要点

1. spawn：`python3 mock_engine.py`（真引擎则换成契约 §1.1 的命令行），stdin/stdout 都是 JSONL。
2. 响应与事件**按 `id` 关联**，不要假设顺序。
3. 一轮结束判定：先等 prompt 的 response，再等其后的第一个 `agent_end`；**不要用 `agent_settled` 当回合边界**（上一轮可能延迟补发，契约 §3.5；driver.py 里演示了正确做法）。
4. 分层结果由**一次模型回合**产出：扩展约束 `brief + detail`，壳负责解析、旧协议兼容与确定性兜底；不得再发 summary prompt。
5. 读屏确认条：收到 `extension_ui_request`(`method:"confirm"`) 弹「读吧/不读」，回 `extension_ui_response`。
