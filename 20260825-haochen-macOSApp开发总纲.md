# haochen macOS App 开发总纲

> 日期：2026-08-25
> 定位：本文件是「haochen 独立 macOS App」阶段的**执行层唯一依据**，取代 `previous-version/haochen-app/下阶段方案.md` 的执行部分（其决策分析仍有效）。
> 前置阅读：`introduction.md`（工作区总览）、`macos-app-dev-reference/MACOS_APP_DEV_REFERENCE.md`（打包规范）、`pi-source/AGENTS.md`（改 pi 源码必读）。

---

## 一、目标：交付一个「可用」的 haochen macOS App

**一句话**：haochen 是独立的「技术专家 agent」macOS 应用——自研引擎（fork pi 源码）+ 完整对话前端 + 配置前端 + 桌面宠物，打包为 `.dmg` 分发，与本机全局 pi 零共享。

### 「可用」的定义（Definition of Done，全部满足才算交付）

1. **对话可用**：聊天窗口流式输出、Markdown 渲染、工具调用过程可见、会话可新建/切换/删除、历史持久化。
2. **桌宠可用**：像素眼镜小哥常驻；双击 / `⌃⌥P` 唤起气泡；提问 →（需要时）读屏确认 → answer/summary 两步 → 气泡短结 + 面板详答。
3. **配置可用**：模型 / API Key / provider / 主题 / 行为设置有 UI，改完即生效，重启后保留。
4. **引擎独立**：`.app` 内嵌 Bun 打包的引擎单文件可执行；不依赖 npm 全局 pi、不读 `~/.pi`、数据全在 `~/Library/Application Support/haochen/`。
5. **分发可用**：`.dmg` 在**另一台干净 mac**（或新用户账户）上能装、能开、能引导授权辅助功能权限、能完成一轮完整问答。
6. **无 Dock 图标常驻**：`LSUIElement=true`，Accessory 形态，符合桌宠定位。

---

## 二、开发路线图（严格按阶段，串行门禁）

每阶段列：目标 / 关键任务 / 产出物 / 验收门禁（不过不进下一阶段）/ 执行策略。

### P0 · 里程碑①：E1 引擎打包验证【串行 · 单 agent】✅ 已完成（2026-08-25）

> **结果**：成功。`engine/haochen-engine`（Bun 1.4.0 编译，59 MB，arm64 单文件）在 /tmp 干净目录跑通 `--mode rpc`：真实模型对话 + bash 工具调用全过（经主会话二次独立复验）。详见 `engine/RESULT.md`。已知限制：TUI/export-html/photon wasm 未打入（RPC 不受影响），`--version` 显示 0.0.0（纯外观）。

- **目标**：Bun 把 pi 核心（coding-agent 的 agent 循环 / 工具 / 会话 / provider / RPC 模式）bundle 成**单文件可执行**，跑通 `--mode rpc`。
- **关键任务**：
  1. `pi-source/` 定位 RPC 入口（`packages/coding-agent`），梳理 bundle 入口点；
  2. `bun build --compile` 产出单文件；处理动态 require / 原生模块 / 资源文件（theme、models 数据）的打包问题；
  3. 无头验证：启动 RPC 进程，发一条 prompt，收到流式事件 + agent_end。
- **产出物**：`engine/haochen-engine`（单文件可执行）+ `engine/build.sh`（可重复构建脚本）。
- **验收门禁**：单文件在**无 node_modules、无全局 pi** 的环境下 `--mode rpc` 完成一轮真实对话（含一次工具调用）。
- **策略**：**单 agent 串行**。bundle 踩坑需要连贯试错上下文，拆开反而慢。预估 2–5 人天。
- **注意**：改 `pi-source/` 遵守其 `AGENTS.md`（改动后 `npm run check`；只提交自己改的文件；不跑全量 vitest）。

### P1 · 契约冻结 + 双探针【并行 · 2 个 agent】✅ 已完成（2026-08-25，产品验收通过）

> **结果**：`docs/rpc-contract.md` v1.0 冻结（事件名/字段经真引擎 dump 双重佐证）；`mock-engine/` 45/45 断言通过、同一驱动打真引擎 9/9 通过；`packaging/spike/` 全链路通过（.app 直出、内嵌二进制 stdio 往返、LSUIElement 无 Dock、ad-hoc 签名验证过）。
> **原最大风险已排除（2026-08-25）**：Bun 二进制直接加载 TS 扩展（jiti virtualModules 路径）实测通过——最小扩展注册命令、typebox 工具 + confirm 确认链路（与契约 §5.2 逐字段一致）、上一版真实 ext/index.ts 两步规则注入，三轮全过。无需 JS 预编译兜底。证据：`mock-engine/verification/ext-test-*`。

P0 通过后立即做，是后续所有并行的前提。

- **目标**：冻结 App 壳 ↔ 引擎的 RPC 接口契约；同时提前排掉打包和通信两颗雷。
- **任务拆分**：
  - **Agent-A（契约 + mock）**：定义 RPC 消息 schema（prompt / 流式事件 / answer·summary 两步 / 读屏确认 / 错误 / 会话管理），写成 `docs/rpc-contract.md`；实现一个 **mock engine**（Python，假数据按契约吐事件），供 UI 开发打桩。
  - **Agent-B（打包探针）**：PyInstaller + PyQt6 的 hello-world `.app`，内嵌一个假二进制作引擎，验证：二进制在 Resources 内可被启动、ad-hoc 签名后能跑、`LSUIElement` 生效。产出 `packaging/spike/` 和结论记录。
  - **产品侧（haochen agent 负责，研发验收）**：提供并冻结两份设计约定——**视觉规范**（色值/描边/圆角/尾巴尺寸/姿态图/字体/动效节奏）与**交互规范**（感知可见/行动可见/结论先行/先问再动/随时可打断/不打扰）。这是 P3 三 agent 并行的前置，防止各做各的跑偏。
- **产出物**：`docs/rpc-contract.md`（**冻结后改动需明示**）、`mock-engine/`、`packaging/spike/` 结论、`docs/visual-spec.md` + `docs/interaction-spec.md`（产品产出，P3 前必须冻结；✅ 已于 2026-08-25 提前交付并通过研发验收）。
- **验收门禁**：mock engine 能被 Python 壳通过 stdio 驱动一轮完整假对话；探针 .app 在 Finder 双击可运行且 Dock 无图标。
- **策略**：两任务文件零重叠，**完全并行**。预估各 1–2 人天。

### P2 · M-B 模型层【串行 · 单 agent】✅ 已完成（2026-08-25）

> **结果**：零 pi 源码改动实现隔离——`PI_CODING_AGENT_DIR=$HAOCHEN_HOME/agent` + `--session-dir` + 独立 cwd 三件套；DeepSeek 配通（key 只读复制）；`~/.pi` 全量快照 diff 零写入；provider 可扩展（加条目重启即生效）。配置 schema 落盘 `config/`（models.json/settings.json/auth.json.template/README，P3 Agent-D 的输入）。契约 v1.1 冻结启动约定。主会话复跑 `engine/p2-isolation-test.py` 全过。

- **目标**：provider / auth / 流式复用 pi 原有逻辑，配通 DeepSeek 等目标模型；配置文件落在 haochen 数据目录。
- **验收门禁**：引擎用配置文件里的 key 完成真实模型对话；换 provider 只改配置。
- **策略**：单 agent，工作量小（1–2 人天），不值得并行。

### P3 · 三 UI 模块【并行 · 3 个 agent，面向契约编程】

前置：P1 契约已冻结。三个模块各管各的目录，共享文件（配置 schema、`rpc-contract.md`）只读不改，改动须收口到主会话串行处理。

- **Agent-C · M-C 对话前端**（5–8 人天，最大头，准关键路径）：
  - 聊天窗口：**动态对话流**（输入/思考/结果逐条弹出的气泡流 + MVP 级动效）、流式渲染、Markdown、工具过程折叠、会话列表（新建/切换/删除/搜索）、输入框（回车发送 / ⌘回车换行）。
  - 全程对 **mock engine** 开发，最后接真引擎。
- **Agent-D · M-D 配置前端**（3–5 人天）：
  - 设置面板：模型 / API Key / provider / 主题 / 行为；持久化到 `~/Library/Application Support/haochen/`；改完热生效或提示重启。
- **Agent-E · M-E 桌宠整合**（3–4 人天）：
  - 复用 `previous-version/haochen-app/haochen/`：像素眼镜小哥 + 姿态、唤起气泡、读屏确认、两步问答、页面变化检测；对 mock engine 调通。
- **验收门禁**：三个前端各自对 mock engine 通过各自的功能清单（见 §四 回归清单对应条目）。
- **策略**：**3 个 agent 并行**，子 agent 无法肉眼验收 Qt 效果——每个 agent 交付时附**截图 + 操作录屏说明**，由用户终审。

### P4 · 集成联调【串行 · 单 agent 主导】✅ 已完成（2026-08-25，待产品验收）

> **结果**：单一入口 `app/run_app.py` + `app_shell.py` 装配三 UI；`supervisor.py` 统一引擎进程管理（退避重启/会话恢复/双入口仲裁）；配置生效链接通（set_model 热切换/重启提示/首启 key 只读导入）。真引擎全链路（真 DeepSeek）16/16 ×2 轮：配置 key→对话→桌宠唤起→读屏确认→两步回答全通；**崩溃重启后切回同一 jsonl、跨重启追问上下文不丢**。修复 mock stdin 预读吞行 bug（L1 45/45 回归）。详见 `docs/p4-delivery-integration.md`。

- **目标**：三 UI + 真引擎合成一个 App 壳（单一入口、进程管理：引擎随 App 启停、崩溃重启）。
- **关键任务**：主进程编排（Qt 壳 spawn 引擎子进程、stdio RPC、心跳/重启）；模块间配置打通（配置 UI 写的配置被引擎读到）。
- **验收门禁**：从 App 内完成「配置 key → 对话 → 桌宠唤起 → 读屏确认 → 两步回答」全链路，对真模型；**气泡与对话窗口共享同一会话，追问不丢上下文**。
- **策略**：串行。集成是冲突密集区，并行只会互相踩。预估 3–5 人天。

### P5 · M-F 打包 dmg【串行 · 单 agent】✅ 已完成（2026-08-25，待产品验收）

> **结果**：`packaging/build.sh` 一键链（读屏执行体 onefile→PyInstaller onedir→资源内嵌→LSUIElement→ad-hoc 签名→dmg）。产出 `dist/haochen.app`（147MB）+ `dist/haochen-0.1.0.dmg`（68MB，含拖装链接）。冻结 App 真机实证：首启配置/key 导入、权限引导卡片（去授权→系统设置直达）、桌宠上屏、双击唤起、**UI 驱动真模型问答（两步标记完整）**、Dock 无图标、签名 verify 过。内嵌读屏执行体独立实测成功（不再依赖 ~/.local/bin/haochen）。详见 `docs/p5-delivery-packaging.md`。

- **目标**：PyInstaller onedir → `.app`（内嵌引擎二进制、`LSUIElement`、Info.plist 权限文案）→ ad-hoc 签名 → `.dmg`。
- **关键任务**：按 `MACOS_APP_DEV_REFERENCE.md` §7 命令链执行；补文档缺口：hardened runtime + entitlements（公证前置）、arch 策略（先 arm64 only）、引擎二进制随 `--deep` 签名。
- **验收门禁**：见 §四 L4 层——干净环境双击安装可用。
- **策略**：串行，复用 P1 Agent-B 的探针结论。预估 4–7 人天（预留弹性，打包历来最折腾）。

### P6 · 回归打磨 + 发布候选【串行为主，QA 并行】✅ 已完成（2026-08-25，待产品终审）

> **结果**：L1–L4 全量回归（`tests/run_all_regressions.sh --real`）PASS=7 FAIL=0；长时稳定（30 轮无卡死、RSS 110→114MB 无泄漏、50 会话堆积、3 次崩溃恢复）7/7；多模型切换（deepseek flash/pro/flash 真答 + 同会话不丢 + 配置持久化）11/11；App 图标换像素小哥（packaging/haochen.icns）；动效参数对齐 visual-spec §5；QA 人肉模拟操作单就绪（docs/qa-script.md）。详见 `docs/p6-delivery-regression.md`。

- **目标**：全量回归、稳定性（长时间运行 / 会话堆积 / 引擎崩溃恢复）、多模型切换回归、**精细动效打磨**（弹性/缓动节奏）。
- **策略**：主 agent 修问题 + **QA agent 分屏人肉模拟**（沿用上一版 M0 的做法，见 §四.3）。
- **发布候选标准**：§一 的 DoD 6 条全过。预估 5–8 人天。

### 路线图总表

| 阶段 | 内容 | 策略 | 预估 | 关键路径 |
|---|---|---|---|---|
| P0 | E1 引擎单文件 | 单 agent | 2–5d | ✅ |
| P1 | 契约 + 双探针 | 2 agent 并行 | 1–2d | ✅（契约） |
| P2 | 模型层 | 单 agent | 1–2d | ✅ |
| P3 | 对话/配置/桌宠 UI | 3 agent 并行 | 5–8d（取最长） | ✅（M-C） |
| P4 | 集成联调 | 单 agent | 3–5d | ✅ |
| P5 | 打包 dmg | 单 agent | 4–7d | ✅ |
| P6 | 回归打磨 | 1 主 + 1 QA | 5–8d | ✅ |

合计约 **21–37 人天**；并行后自然日预计 **3–5 周**。关键路径：P0 → P1(契约) → P3(M-C) → P4 → P5 → P6。

> **✅ 全部阶段已完成（2026-08-25）**：产品终审判定「可交付上市」，收口标记 `.dev-loop-done` 已置位。交付归档索引见 `docs/00-交付归档索引.md`。外发/他人安装需 Developer ID + 公证（§五 既定）；本机/内测 ad-hoc 可用。

---

## 三、开发原则

1. **E1 不动摇**：引擎 = Bun bundle pi 源码，逻辑零改写。禁止转向 Python 重写（E2，60–90 人天，是否决项）。发现 bundle 障碍时逐个解决，不换路线。
2. **契约先行，面向接口编程**：P1 的 `rpc-contract.md` 冻结后，UI 对 mock 开发。契约变更必须在文档中显式标注并通知所有并行模块。
3. **最小改动，不发明需求**：每个阶段只交付该阶段验收门禁要求的东西。不做「以后可能用到」的抽象。贴边露头（U2）、过度美化、**精细动效**不在本期；但**动态对话流与 MVP 级动效（气泡逐条弹出、桌宠姿态切换、流式打字机）是核心交互形态，纳入 P3**（产品审查对齐项，非美化范畴）。
4. **pi-source 纪律**：改 pi 源码必读其 `AGENTS.md`——改动后 `npm run check` 并修清所有报错；测试只跑 `./test.sh` 或指定文件，不跑全量 vitest；git 只 `git add <明确路径>`，禁 `git add -A`；commit message 用其格式，无 emoji。
5. **验证才算完成**：任何「完成」声明前必须实际运行验证（进程起来、对话走通、截图在）。不许「应该能跑」。
6. **隔离原则**：不碰全局 pi 配置、不写 `~/.pi`；数据 / 配置 / 会话全在 `~/Library/Application Support/haochen/`；与上一版 `previous-version/` 只读复用，不在原目录改。
7. **子 agent 使用规范**：
   - 每个子 agent 的 prompt 必须自带：任务目标、契约文档路径、允许动的文件/目录清单、验收标准；
   - 文件所有权互斥：并行 agent 之间目录零重叠，共享文件改动收口主会话；
   - 子 agent 产出须附：改动文件清单 + 验证证据（命令输出 / 截图）；
   - GUI 效果一律用户终审，子 agent 的「我觉得好看」不算数。
8. **文档同步**：每个阶段结束时更新本文件状态；新增技术约定写进 `docs/`。文档与代码冲突时以代码事实为准并当场修文档。
9. **git 纪律**：未经用户明确要求不 commit；commit 按阶段小步提交，message 中文、说清为什么。

---

## 四、测试方案

### 1. 分层测试

| 层 | 对象 | 方法 | 自动化 |
|---|---|---|---|
| **L1 引擎协议** | 引擎单文件 + RPC 契约 | Python 无头测试脚本：spawn 引擎 → 发 prompt → 断言事件序列（流式 / answer / summary / 工具调用 / 错误路径） | ✅ 脚本化，每阶段回归必跑 |
| **L2 单元** | Python 壳的关键纯逻辑（协议解析、配置读写、会话管理） | pytest | ✅ |
| **L3 UI 功能** | 三前端 + 集成 App | 人工按回归清单点 + 截图；QA agent 人肉模拟（见下） | ❌ 人工/半自动 |
| **L4 分发** | .dmg 成品 | 干净环境（新用户账户或另一台 mac）：下载 quarantine → 双击 → Gatekeeper → 权限引导 → 完整问答 | ❌ 人工 |

### 2. 阶段门禁与测试的对应

- P0 → L1 脚本首轮跑通；
- P1 → L1 脚本同时打真引擎和 mock engine，同一套断言都过（证明 mock 可信）；
- P2 → L1 脚本用真实 provider 配置跑通；
- P3 → 各前端对 mock 过 L3 清单；L1/L2 持续回归；
- P4 → L3 全链路清单对真引擎过一遍；
- P5 → L4 全过；
- P6 → L1+L2+L3+L4 全量回归。

### 3. QA agent 人肉模拟（沿用上一版 M0 做法）

分屏开独立 agent 当测试员：只给它用户视角的操作说明（启动 App、提问、确认读屏、切换会话、改配置、重启 App），不给实现细节；它汇报操作 + 现象 + 截图，与预期比对。每轮 P4/P6 至少跑一轮完整 QA loop。

### 4. 核心回归清单（每轮 QA / 发布前必过）

1. 首次启动：权限引导（辅助功能）出现且指引正确；
2. 配置：填 key → 保存 → 重启 App → 配置还在；
3. 对话：提问 → 流式输出 → Markdown 正确 → 工具过程可见；
4. 会话：新建 / 切换 / 删除 / 重启后历史还在；**气泡与对话窗口为同一会话，追问不丢上下文**；
5. 桌宠：双击 / `⌃⌥P` 唤起；读屏确认弹窗「读吧/不读」均工作；answer→summary 两步；气泡短结 + 面板详答；Esc / 点空白收起；
6. 引擎健壮性：kill 引擎进程 → App 能感知并重启引擎或明确报错，不白屏；
7. 隔离性：运行期间 `~/.pi` 无写入、全局 pi 配置无变化；
8. 分发：干净环境安装 → 以上 1–6 抽测；
9. **视觉统一**：三 UI 符合 `docs/visual-spec.md`（色值 / 描边 / 圆角 / 尾巴 / 字体 / 动效节奏）；
10. **体验元规则**：读屏进行中有轻提示（感知可见）、失焦不自动收起、idle 不打扰（不误伤）。

### 5. 测试环境约定

- 开发期 App 数据目录用环境变量指到临时目录（如 `HAOCHEN_HOME=/tmp/haochen-test`），避免污染真实数据；
- L1 脚本和回归清单入库（`tests/`），随代码演进；
- 真实模型调用只在验收时做（花钱），日常开发用 mock + 廉价模型。

---

## 五、风险登记（Top 5）

| 风险 | 影响 | 对策 |
|---|---|---|
| Bun bundle pi 时踩动态 require / 资源文件坑 | P0 延期 | 单 agent 连贯攻坚；最坏情况：bundle JS + 内嵌 Bun 运行时（仍满足「单文件可执行」） |
| RPC 契约考虑不周，P3 并行期返工 | 三 UI 一起返工 | P1 冻结前用 L1 脚本把事件序列穷举一遍；契约变更走显式流程 |
| PyInstaller + PyQt6 + 内嵌二进制打包地狱 | P5 延期 | P1 探针提前排雷；预留弹性；先 arm64 only |
| 子 agent 并行产出质量参差 / 互相踩文件 | 集成期爆雷 | 文件所有权互斥 + 契约冻结 + 主会话收口集成（P4 串行） |
| 公证 / Gatekeeper（分发最后一关） | 用户装不上 | 本期先 ad-hoc + `xattr` 说明自用；Developer ID 证书（$99/年）在决定外发时再买 |

---

## 附：相关文档索引

- `introduction.md` — 工作区总览、产品三段演进
- `previous-version/haochen-app/下阶段方案.md` — 决策分析（E1/E2/E3 选型依据）
- `previous-version/haochen-app/阶段总结.md` — 上一版已完成能力与坑
- `macos-app-dev-reference/MACOS_APP_DEV_REFERENCE.md` — 打包 / 签名 / 权限规范（P5  checklist）
- `pi-source/AGENTS.md` — pi 源码开发纪律
