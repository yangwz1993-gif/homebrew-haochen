# mock-engine 演示与验证证据

> 日期：2026-08-25 ｜ 环境：macOS arm64，Python 3.9.6（系统自带，无第三方依赖）

## 1. 驱动全流程（门禁：发 prompt → 收流式 → 收工具确认 → 授权 → 同回合收 brief + detail）

命令：

```bash
cd mock-engine
MOCK_TICK_MS=10 python3 driver.py --dump verification/mock-demo.jsonl
```

退出码 0 即全部断言通过。`verification/demo-output.txt` 保留 v1.0 两回合协议的历史基线；当前结果以实时运行 `driver.py` 为准。

```
[PASS] 普通对话: prompt 受理成功
[PASS] 普通对话: agent_start 开头 — ['agent_start', 'turn_start', 'message_start']
[PASS] 普通对话: 含 turn_start/turn_end
[PASS] 普通对话: 有流式 message_update
[PASS] 普通对话: 含配对【brief】标记
[PASS] 普通对话: 含配对【detail】标记
[PASS] 普通对话: brief 可独立显示
[PASS] 普通对话: detail 可展开查看
[PASS] 读屏: tool_execution_start(read_screen)
[PASS] 读屏: confirm 标题/文案符合契约
[PASS] 读屏: 授权后工具成功（isError=false）
[PASS] 读屏: 含配对【brief】标记
[PASS] 读屏: 含配对【detail】标记
[PASS] 错误: assistant stopReason=error + errorMessage
[PASS] 会话: new_session 成功 / get_messages / set_session_name / switch_session …
[PASS] 未知命令: success=false + error
==== 36/36 通过 ====
```

四个场景全部走通：

1. **普通对话 + 分层结果**：driver 发 prompt → 收 thinking/text 流式 → `agent_end` → 同一正文解析【brief】与【detail】，不再追加内部 prompt。
2. **读屏确认**（门禁主流程）：prompt 含「屏幕」→ `tool_execution_start(read_screen)` → `extension_ui_request`(confirm) → driver 回 `{"confirmed":true}` → `tool_execution_end` isError=false → brief + detail。
3. **错误路径**：prompt 含「错误」→ assistant `stopReason:"error"` + `errorMessage`，`agent_end.willRetry=false` 正常收尾。
4. **多会话**：get_state → new_session → 第二会话对话 → get_messages（2 条）→ set_session_name → switch_session 切回 → switch 不存在的会话报错。

全部 352 行原始事件录在 `verification/mock-demo.jsonl`。读屏确认链路实录：

```json
{"type":"extension_ui_request","id":"b94a62dc-…","method":"confirm","title":"haochen 想读屏","message":"需要读取当前锁定窗口的屏幕内容才能回答。点「读吧」或在输入框回 y 允许；回 n 拒绝。","timeout":120000}
{"type":"tool_execution_end","toolCallId":"mock_call_b3a715b1","toolName":"read_screen","result":{"content":[{"type":"text","text":"窗口来源：[Safari] Mock 示例页面 — haochen\n…"}],"details":{…}},"isError":false}
```

## 2. 同一驱动打真引擎（协议级断言）

命令（会真实调用一次廉价模型）：

```bash
python3 driver.py --engine ../engine/haochen-engine --real --dump verification/real-engine-driver.jsonl
```

退出码 0，9/9 全过：

```
[PASS] 真引擎普通对话: prompt 受理成功
[PASS] 真引擎普通对话: agent_start 开头 — ['agent_start', 'turn_start', 'message_start']
[PASS] 真引擎普通对话: 含 turn_start/turn_end
[PASS] 真引擎普通对话: 含 message_start/message_end
[PASS] 真引擎普通对话: 有流式 message_update
[PASS] 真引擎普通对话: agent_end.willRetry=false
[PASS] 真引擎: assistant 文本非空 — pong
[PASS] 真引擎: message_update 含 usage + assistantMessageEvent — 4 条
[PASS] 真引擎: assistantMessageEvent 无 partial 字段（线上剥形）
==== 9/9 通过 ====
```

注：真引擎未加载 haochen 扩展时不产 `【brief】/【detail】` 标记，属预期（分层规则由扩展注入，契约 §4），故 --real 模式不断言标记。

## 3. 契约事件形状的真机依据

| 文件 | 内容 |
|---|---|
| `verification/real-engine-dump.jsonl` | 真引擎普通对话 30 行全事件（DeepSeek 廉价 prompt，2026-08-25） |
| `verification/real-engine-dump-tool.jsonl` | 真引擎 bash 工具调用 73 行全事件（含 toolcall_*/tool_execution_* 真实字段） |
| `verification/real-engine-driver.jsonl` | 驱动打真引擎的原始录屏 |

mock 的事件名、字段名、回合骨架（agent_start → turn_start → message_start → message_update×N → message_end → [tool_execution_* → toolResult] → turn_end → agent_end{willRetry} → agent_settled）逐一对照上述 dump 编写。
