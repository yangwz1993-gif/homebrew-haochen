# E1 引擎打包验证 — 结果报告

## 结论

**成功。** pi v0.84.3 monorepo 源码用 Bun 1.4.0 打包为单文件可执行 `haochen-engine`（59 MB, macOS arm64），在 /tmp 干净目录（无 node_modules、不依赖全局 pi）下通过 `--mode rpc` 完成：

1. 一轮真实模型对话（DeepSeek，流式事件 + 结束事件）；
2. 一轮工具调用（bash 工具执行 `echo hello-from-tool` 并正确回报输出）。

## 产出文件

| 文件 | 说明 |
| --- | --- |
| `engine/haochen-engine` | 单文件可执行（Bun compiled binary, 59 MB） |
| `engine/build.sh` | 一键复现构建脚本（已实测可重复构建） |
| `engine/rpc-smoke-test.mjs` | RPC 冒烟驱动脚本（JSONL 协议，含工具调用轮） |
| `engine/RESULT.md` | 本报告 |

## 做法

### 构建方案

关键发现：**不需要先构建各包 dist**。pi-source 根 `tsconfig.json` 自带完整 `paths` 映射（`@earendil-works/*` → `packages/*/src`），Bun 按"逐文件向上查找 tsconfig"的规则解析路径，因此可直接从 TS 源码整树打包。入口沿用官方 `build:binary` 的用法：

```bash
cd packages/coding-agent
bun build --compile --no-compile-autoload-bunfig --no-compile-autoload-dotenv \
  --target=bun-darwin-arm64 \
  ./src/bun/cli.ts ./src/utils/image-resize-worker.ts \
  --outfile <engine>/haochen-engine
```

- `--no-compile-autoload-bunfig`：防止二进制在任意 cwd 下加载项目 bunfig 崩溃（上游 #7684 同款）。
- `image-resize-worker.ts` 必须作为显式入口，否则 worker 不会嵌入二进制。
- 全程未运行 `npm run build` / 全量 vitest；依赖用 `npm install --ignore-scripts` 安装。

### RPC 验证方法

`rpc-smoke-test.mjs` 在干净目录 spawn `./haochen-engine --mode rpc --no-extensions`，stdin 发 JSONL：

```json
{"id":"p1","type":"prompt","message":"Reply with exactly one word: pong"}
```

每轮等待两件事：对应 id 的 `{type:"response", success:true}`，以及**之后**出现的 `agent_end`/`agent_settled`。模型/provider 走默认配置（`~/.pi`，只读未修改）。

## 踩坑与解法

1. **`packages/ai/src/providers/data/*.json` 缺失**：源码树不附带生成数据，bun build 报 `Could not resolve ./data/github-copilot.json`。解法：运行 `node packages/ai/scripts/generate-models.ts` 生成。

2. **models.dev 被 DNS 污染，脚本直连失败**：`generate-models.ts` 用 Node fetch 直连 `https://models.dev/api.json`，不走代理，首次运行超时；非 strict 模式下**会删除拿不到数据的 provider 的 `.models.ts` 分片**（破坏性）。解法：curl 下载 `api.json`，临时给脚本打"读本地文件"补丁（`MODELS_DEV_API_JSON` 环境变量），生成 39 个分片后**已恢复脚本原样**。

3. **theme JSON 启动即读，二进制旁无文件直接崩**：`main()` → `initTheme()` → `getBuiltinThemes()` 读 `<可执行文件目录>/theme/dark.json`，ENOENT 崩溃。解法（唯一的代码改动）：`theme.ts` 增加打包期内嵌回退 —— 静态 `import dark.json/light.json with { type: "json" }`，磁盘文件不存在时用内嵌副本。磁盘优先，不影响 Node/dist 形态行为。

4. **models.dev 目录漂移导致 `npm run check` 挂**：用"今天的" models.dev 数据再生成后，Cloudflare workers-ai 目录里 `kimi-k2.6` 已被 models.dev 下架，而 ai 包测试和 `cloudflare-ai-gateway.ts` 的类型依赖该条目，tsgo 报 10 个错。GitHub v0.84.3 tag 与 main 当前的 checked-in 数据同样缺它（上游当前即处于不一致状态）。解法：从 **npm 发布的 `@earendil-works/pi-ai@0.84.3`** tarball 的 `dist/providers/data/`（发布时生成的目录，含 kimi-k2.6）整体恢复 `src/providers/data/`，之后 `npm run check` 全绿。

5. **RPC 驱动时序坑**：`agent_settled` 会在上一轮 `agent_end` 之后延迟触发一次，简单地"等下一个 settle"会被旧事件提前唤醒。驱动脚本改为先等 prompt 的 response，再等 settle 计数递增。

6. **Bun tsconfig paths 是逐文件发现的**：曾验证不能用外部 tsconfig 注入 paths（入口目录的 paths 不管被导入文件），因此确认必须依赖 pi-source 根 tsconfig 自带的 paths —— 恰好够用。

## 验证证据

环境：`/tmp/engine-test2/`（新建干净目录，仅含二进制），驱动脚本以该目录为 cwd。

```
[response] id=p1 command=prompt success=true
=== round 1: plain prompt ===
events: {"agent_start":1,"turn_start":1,"message_start":2,"message_end":2,"message_update":4,"turn_end":1,"agent_end":1}
assistant text: "pong"

[response] id=p2 command=prompt success=true
[tool] bash isError=false result={"content":[{"type":"text","text":"hello-from-tool\n"}]}
=== round 2: tool call (bash) ===
events: {"agent_start":1,"turn_start":2,"message_start":4,"message_end":4,"message_update":28,
         "turn_end":2,"agent_end":1,"agent_settled":1,
         "tool_execution_start":1,"tool_execution_update":2,"tool_execution_end":1}
assistant text: "The command printed: `hello-from-tool`"
tools this round: bash
SUMMARY {"settleCount":3,"toolCalls":["bash"]}   # 驱动退出码 0
```

复现步骤：

```bash
cd "<repo>/engine" && ./build.sh                     # 构建
rm -rf /tmp/engine-test && mkdir /tmp/engine-test
cp haochen-engine /tmp/engine-test/ && cd /tmp/engine-test
node "<repo>/engine/rpc-smoke-test.mjs" ./haochen-engine --tool-test
```

## pi-source 改动清单（`npm run check` 已全绿，未做任何 commit）

- `packages/coding-agent/src/modes/interactive/theme/theme.ts` — 唯一的代码改动：内嵌 theme JSON 回退（见坑 3）。
- `packages/ai/src/providers/data/` — 新增生成物目录，内容取自 npm `@earendil-works/pi-ai@0.84.3` 发布数据（见坑 4）。
- `packages/ai/src/providers/*.models.ts`、`packages/ai/src/models.generated.ts` — 经 `generate-models.ts` 再生成，逐字节与上游 v0.84.3 tag 一致（AGENTS.md 明确允许该文件 diff）。
- `node_modules/` — `npm install --ignore-scripts` 产物；`package-lock.json` 与上游 v0.84.3 逐字节一致，未被改动。
- 注：pi-source 不是 git 仓库（无 .git），以上结论基于与 GitHub `earendil-works/pi-mono` v0.84.3 tag tarball 的 `diff -r` 对比。

`npm run check` 最终输出：biome 1094 文件无修复、pinned-deps/ts-imports/shrinkwrap/install-lock/tsgo/browser-smoke 全部通过，退出码 0。

## 已知限制

- `./haochen-engine --version` 显示 `0.0.0`：二进制旁无 package.json，`config.ts` 回退默认值。纯外观问题，不影响 RPC。需要时可放一份 package.json 在旁边。
- **photon wasm 未随二进制分发**：图片缩放（photon）首次使用时会因缺 `photon_rs_bg.wasm` 失败（`loadPhoton()` 捕获返回 null，不崩溃）。纯文本/工具调用不受影响。需要时把 wasm 放到可执行文件旁即可（代码已有该回退路径）。
- **交互 TUI 未验证**：主题已内嵌回退可启动，但 clipboard（`@mariozechner/clipboard`）与 `darwin-modifiers.node` 原生模块未打进二进制，交互模式的剪贴板/修饰键可能降级。E1 门禁只要求 RPC。
- **export-html / docs / examples 未随二进制分发**：RPC 的 `export_html` 命令会因缺模板失败；`/help` 等读 docs 的功能同理。
- RPC 运行会在 `~/.pi/agent/sessions/` 写会话文件（pi 自身正常行为，任何 pi 运行都会写）；配置只读未改。
- ~~`--no-extensions` 下验证通过；加载用户扩展（jiti 动态编译）未验证。~~ → 已在 P1 补验通过，见下节。

## 追记（2026-08-25，P1 Agent-A）：TS 扩展加载实测 —— 通过

**结论：Bun 编译单文件可直接用 `-e` 加载 TypeScript 扩展（jiti 动态编译路径在编译二进制上可用），无需 JS 预编译兜底。**

源码依据：`packages/coding-agent/src/core/extensions/loader.ts` 中 `isBunBinary` 时 jiti 以 `virtualModules` 提供 `typebox`、`@earendil-works/pi-*`、`@mariozechner/pi-*`（这些包被静态 import 进二进制），`tryNative:false`；`--no-extensions` 只禁自动发现，显式 `-e` 仍加载（`resource-loader.ts` / `args.ts`）。

实测三轮（/tmp 干净 cwd，`HAOCHEN_PET=1`，证据在 `mock-engine/verification/`）：

1. **最小 TS 扩展**（含 `interface`/类型注解，强制经过 TS 编译）：factory 执行、命令注册成功，`get_commands` 返回 `mockping`（`source:"extension"`）。证据：`ext-test-min-ext.{stdout.jsonl,stderr.log}`。
2. **typebox 运行时导入 + 工具 + confirm 链路**：扩展 `import { Type } from "typebox"` 成功（virtualModules 命中）；注册 `mock_confirm_tool`；真实模型调用该工具 → `ctx.ui.confirm` 转出 `extension_ui_request{method:"confirm"}` → 壳回 `extension_ui_response{confirmed:true}` → `tool_execution_end` 返回「用户点了确认」→ 模型正确转述。**契约 §5 读屏确认链路在真二进制上端到端成立**（一次廉价 prompt）。证据：`ext-test-confirm.{stdout.jsonl,stderr.log}`。
3. **上一版真实扩展 `previous-version/haochen-app/ext/index.ts` 原样加载成功**：`pet` 命令与 `read_screen` 工具注册（`get_commands` 含 `pet`，无 stderr 报错）；再用一条廉价 prompt 验证 `before_agent_start` 注入 ANSWER_RULES 生效——模型输出严格以配对 `【answer】…【/answer】` 包裹。**两步协议的规则注入半在真二进制上成立**。证据：`ext-test-real-ext.{stdout.jsonl,stderr.log}`、`ext-test-twostep.stdout.jsonl`。

遗留未测（不影响结论）：summary 回合由壳踢 `haochen-summary-phase` prompt 触发，属壳侧逻辑（mock/driver 已演示）；`read_screen` 真实执行依赖 petread 二进制与辅助功能授权，属 P4 集成范围。

## 追记（2026-08-25，P2）：M-B 模型层 + 数据隔离 —— 通过

**结论：引擎完全脱离 `~/.pi` 运行，仅需 pi 原生 env/flag，零源码改动。**

### 隔离方案

- `PI_CODING_AGENT_DIR=<HAOCHEN_HOME>/agent`（`config.ts: ENV_AGENT_DIR`）：auth.json / models.json / settings.json / themes / bin / 默认会话目录全部重定向。
- `--session-dir <HAOCHEN_HOME>/pi-sessions`：会话文件目录（优先级：flag > `PI_CODING_AGENT_SESSION_DIR` env > settings.json `sessionDir` > agent/sessions/）。
- 进程 cwd = `<HAOCHEN_HOME>/pi-home`（空目录）：项目级 `.pi` 资源发现落在自己目录。
- `HAOCHEN_HOME` 默认 `~/Library/Application Support/haochen`，开发/测试指向临时目录。
- DeepSeek key 从 `~/.pi/agent/auth.json` **只读复制** deepseek 条目；models.json/settings.json 用 `config/` 模板（settings 里 `enableInstallTelemetry:false`、`enableAnalytics:false`）。

### 验证（`engine/p2-isolation-test.py`，可重复跑）

HAOCHEN_HOME=/tmp/haochen-p2-test（全新临时目录），跑前/跑后对 `~/.pi` 全量快照（path+size+mtime+sha256）：

```
[PASS] 真实对话成功（DeepSeek，隔离目录 auth） — pong
[PASS] sessionFile 落在 $HAOCHEN_HOME/pi-sessions — /tmp/haochen-p2-test/pi-sessions/….jsonl
[PASS] 默认模型来自隔离 models.json — deepseek-v4-flash-vision-exp
[PASS] pi-sessions 下有会话文件
[PASS] ~/.pi 零写入（无新增/删除/修改） — added=[] removed=[] changed=[]
[PASS] 第二 provider 条目生效（get_available_models 列出） — providers=['deepseek', 'mock-co']
[PASS] deepseek 模型仍在
==== 全部通过 ====（退出码 0）
```

### 调研结论（供后续阶段）

- 配置三文件 schema：`auth.json` `{providerId:{type:"api_key",key}}`（key 支持 `$ENV_VAR` / `!cmd` 间接引用，`resolve-config-value.ts`）；`models.json` providers 结构（`model-config.ts: ProviderConfigSchema`）；`settings.json`（`settings-manager.ts: Settings`）。详见 `config/README.md`。
- **`get_available_models` 只列凭证就绪的 provider**（`model-runtime.ts`：`available = checkAuth 通过者`）——新增 provider 必须带 apiKey 或 auth.json 条目才会出现在列表。配置 UI 注意先后顺序。
- 换 provider 只改 models.json（加一个 providers 键）；运行期换模型用 RPC `set_model` 热切换；新增 provider 需重启引擎。
- 可选项（P5 打包期再议）：引擎二进制旁放带 `piConfig` 的 package.json 可把 APP_NAME 改为 haochen、configDir 默认指到 Application Support，并顺带修复 `--version` 显示 0.0.0。本期不依赖。
