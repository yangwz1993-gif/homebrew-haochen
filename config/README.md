# haochen 配置层说明（M-B 模型层 / 数据隔离）

> P2 产物。面向：P3 配置前端（Agent-D，本文件是配置 schema 的唯一依据）、P4 集成（壳 spawn 引擎的 env/参数约定）。
> 与 `docs/rpc-contract.md` v1.1 配套；契约的 §1.1 启动参数以本文件为准做了细化。

## 1. 数据目录布局（隔离方案）

haochen 与全局 pi（`~/.pi`）**零共享**。一切数据落在 `HAOCHEN_HOME`：

```
HAOCHEN_HOME/                         # 默认 ~/Library/Application Support/haochen
├── agent/                            # = 引擎的 PI_CODING_AGENT_DIR
│   ├── auth.json                     # provider API key（见 §3）
│   ├── models.json                   # provider / 模型定义（见 §4）
│   ├── settings.json                 # 引擎设置（见 §5）
│   ├── sessions/                     # （默认会话目录；haochen 用 --session-dir 指到下面）
│   └── …（引擎自建：bin/ themes/ 等，勿动）
├── pi-sessions/                      # 会话 jsonl（壳侧 list/delete 扫这里，契约 §2.8）
└── pi-home/                          # 引擎进程 cwd（项目级 .pi 发现落在这，天然隔离）
```

**壳 spawn 引擎的约定**（已在真机验证，见 `engine/RESULT.md` P2 节）：

```bash
HAOCHEN_HOME=${HAOCHEN_HOME:-$HOME/Library/Application Support/haochen}
env PI_CODING_AGENT_DIR="$HAOCHEN_HOME/agent" \
    HAOCHEN_PET=1 \
  haochen-engine --mode rpc --no-extensions -e <haochen-ext.ts> \
    --session-dir "$HAOCHEN_HOME/pi-sessions"
# cwd 设为 "$HAOCHEN_HOME/pi-home"
```

- `PI_CODING_AGENT_DIR`（pi 原生 env，`config.ts: ENV_AGENT_DIR`）：一行解决 auth/models/settings/sessions/themes/bin 全部重定向，**无需改 pi 源码**。
- 会话目录优先级：`--session-dir` flag > `PI_CODING_AGENT_SESSION_DIR` env > settings.json `sessionDir` > `agent/sessions/`。haochen 固定用 flag。
- 开发/测试期把 `HAOCHEN_HOME` 指到临时目录即可（总纲 §四.5），上面命令无需改动。
- 引擎二进制旁放 `package.json` 的 `piConfig` 改名方案（`APP_NAME`→haochen）属于 P5 打包期可选项，本阶段不依赖。

## 2. 首次启动 / 配置迁移

壳的职责（P4 实现，此处冻结约定）：

1. `HAOCHEN_HOME/agent/` 不存在 → 用本目录三个模板初始化。
2. **key 存储**：只存于 macOS Keychain（service `com.haochen.app.api-key.v2`）。`auth.json` 中仅写 `"$HAOCHEN_<PROVIDER>_API_KEY"` 环境引用，壳 spawn 引擎时通过 `export_keychain_credentials` 把实际值注入子进程环境。启动和状态检查使用禁止系统授权 UI 的读取路径；旧签名或锁定条目只会引导用户在 haochen 内重新填写，不会弹出阻塞的 `SecurityAgent`。写入前先经固定安全端点验证，失败不覆盖旧 Key。**不读取也不导入全局 `~/.pi` 凭据。**
3. `models.json` 直接用本目录模板（已含 deepseek 三个模型定义，含默认的 `deepseek-v4-flash-vision-exp`）。
4. 首次启动由单一可续办向导（`app/haochen_app/onboarding.py`）依次完成欢迎→Key 验证→按需权限→试问；向导状态存 `HAOCHEN_HOME/onboarding-state.json`（0600）。

## 3. `auth.json` schema

```json
{
  "<providerId>": { "type": "api_key", "key": "<key>" }
}
```

- `providerId` 须与 `models.json` 的 provider 键一致（内建 provider 如 `deepseek` 直接用内建 baseUrl）。
- `key` 支持间接引用（pi 原生，`resolve-config-value.ts`）：`"$ENV_VAR"` 从环境变量读；`"!command"` 执行命令取 stdout。**配置 UI 通过 Keychain 写入，`auth.json` 只保存 haochen 托管的 `$HAOCHEN_*_API_KEY` 引用**；外部间接引用留给高级用户手动维护，面板只读展示。
- OAuth 型：`{"type":"oauth","access","refresh","expires"}`（haochen 本期不用）。

## 4. `models.json` schema（换 provider 只改这里）

```json
{
  "providers": {
    "<providerId>": {
      "name": "显示名（可选）",
      "baseUrl": "https://…/v1        // 内建 provider 可省略",
      "api": "openai-completions      // 内建可省略；自定义端点必填",
      "apiKey": "<key 或 $ENV 或 !cmd> // 可选；省略则读 auth.json",
      "headers": {"…": "…"},           // 可选，自定义请求头
      "compat": {…},                   // 可选，协议兼容开关（见下）
      "models": [
        {
          "id": "model-id",            // 必填
          "name": "显示名",
          "api": "…", "baseUrl": "…",  // 可选，覆盖 provider 级
          "reasoning": true,
          "thinkingLevelMap": {"high": "high", "xhigh": "max", "minimal": null, "low": null, "medium": null},
          "input": ["text", "image"],
          "cost": {"input": 0.14, "output": 0.28, "cacheRead": 0.0028, "cacheWrite": 0},
          "contextWindow": 1000000,
          "maxTokens": 384000,
          "compat": {"thinkingFormat": "deepseek", "requiresReasoningContentOnAssistantMessages": true}
        }
      ],
      "modelOverrides": {"<modelId>": {…}}   // 可选，覆盖内建目录里的已知模型
    }
  }
}
```

- **加第二个 provider = 在 `providers` 里加一个键**（含 baseUrl/api/models），配置 UI 加一个表单区块即可，引擎无需任何改动（P2 已验证：加条目后 `get_available_models` 立即列出）。
- `api` 取值：`openai-completions` / `openai-responses` / `anthropic-messages` / `azure-openai-responses` / `google-generative-ai` 等（pi-ai 内建目录）。
- `compat` 常用键：`thinkingFormat`（`deepseek`/`openai`/`qwen`/…）、`supportsUsageInStreaming`、`requiresToolResultName` 等；不确定就不写，pi 有 URL 自动探测。
- 模板 `config/models.json` 自带 deepseek 三模型（flash / pro / flash-vision-exp），字段取自生产可用的真实配置。

## 5. `settings.json` schema（haochen 用到的子集）

| 字段 | 类型 | 说明 |
|---|---|---|
| `defaultProvider` / `defaultModel` | string | 默认模型（配置 UI 的核心写入目标） |
| `defaultThinkingLevel` | `"off".."max"` | 默认思考档位 |
| `modelThinkingLevels` | map | 按 `provider/modelId` 覆盖思考档 |
| `theme` | string | 引擎 TUI 主题（RPC 模式无感，留 `dark`） |
| `enableInstallTelemetry` | bool | **haochen 固定 false**（默认 true 会发版本 ping） |
| `enableAnalytics` | bool | **haochen 固定 false** |
| `sessionDir` | string | 会话目录（haochen 用 CLI flag，不写这里） |
| `retry` / `compaction` | object | 自动重试/压缩策略（默认值即好用，UI 不暴露） |
| `httpProxy` | string | 代理（可选暴露） |

完整字段见 `pi-source/packages/coding-agent/src/core/settings-manager.ts: Settings`。配置 UI 只暴露：默认模型、key、（可选）代理与思考档；其余保持模板默认。

## 6. RPC 运行期换模型

契约 §2 之外补充两个已存在但未冻结进契约的命令（P3 配置 UI 可用）：

- `{"id":"x","type":"get_available_models"}` → `data.models[]`（读 models.json + 内建目录）
- `{"id":"x","type":"set_model","provider":"deepseek","modelId":"…"}` → 热切换当前会话模型

**注意**：`get_available_models` 只列**凭证就绪**的 provider（`model-runtime.ts`：`available = checkAuth 通过者`）。新增 provider 若既无 `apiKey` 也无 auth.json 条目，不会出现在列表里——配置 UI 应在「填 key」与「列出模型」之间建立这个先后关系。

「改完即生效」路径：配置 UI 写 models.json/auth.json/settings.json → `set_model` 热切换；**新增 provider 需重启引擎**（模型目录启动时加载）。
