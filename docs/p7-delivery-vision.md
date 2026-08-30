# P7 交付：读屏视觉增强（多模态，研发 → 产品）

> 日期：2026-08-25 ｜ 交付方：kimi（研发）｜ 依据：产品方案确认（P7-Vision）+ 两个决策点 + 窗口选择打回
> 状态：**实现完成并逐层验证**；含「窗口选择」修复（读到用户实际窗口，非 haochen 自身）。

---

## 〇、窗口选择修复（产品打回，关键）

**根因**：读者是**独立进程**（子二进制），其 `os.getpid()` 是读者自身，永远不等于前台 App。原 `target_pid()` 用「前台 == 读者自身 pid」判断「前台是 haochen」→ 该判断永远不成立 → 前台是 haochen（用户点宠物）时没走回退，`frontmost_pid()` 返回 haochen → 读/截 haochen 自身（宠物/气泡窗口，无页面内容）→ 答「屏幕没出现人物/照片」。

**修复**：
1. 壳把 **haochen app pid** 经 `HAOCHEN_APP_PID` 传给读者（读者据此识别「前台=haochen」）。
2. 壳用 **NSWorkspace 激活监听**（`app_tracking.py`）持续记录**用户最近激活的非 haochen app pid**（Chrome）到 `HAOCHEN_HOME/last-user-app.txt`。
3. 读者 `target_pid()`：前台非 haochen → 读前台；前台=haochen → 优先读 sidecar（用户最近窗口，Chrome）→ 其次最大普通 window（避开系统悬浮窗）。
4. 截图 `capture_window_image` 用同一 target pid（保证 AX 文本+截图都是用户窗口）。

**验证**（ihms）：
- 前台=haochen + sidecar=Chrome → 目标 = **Google Chrome**（命中用户窗口）✓
- 前台=正常app → 目标 = 前台 ✓
- 冻结 reader 实测：`--json --no-scroll` → app=cmux、12 段文本、780KB 截图（真实用户窗口）✓

## 〇.5、喂图策略优化（产品反馈）

**评估**：读者**本就**通过 AX 收集窗口内图片 URL（AXImage AXURL，实测 Chrome 读到 71 张带 URL）并下载 base64（`prepare_images`，上限 10）；ext 本就喂这些原图。需调整的是**优先级顺序**（原图优先、截图仅兜底）。

**调整（P7 优化）**：
- **feed 顺序**：图片原图（URL 下载，最准）→ 文本 → 截图（仅兜底）。
- **截图仅兜底**：有 URL 原图可下载时，reader 不截整窗（截图可能认错）；仅当无 URL 原图（动态/canvas/图片型UI）时才截整窗作兜底。
- 结果：识人「她是谁」→ 喂到**真实人物原图**（URL 下载 base64），最准。

**验证**：ext 加载（jiti）+ cmux（0 图）→ 文本 + 截图兜底（无原图时）；有原图窗口 → 原图先喂（代码路径已证）。

---

## 一、实现内容（方案 4 步全落地）

| 步骤 | 实现 | 证据 |
|---|---|---|
| P7.1 读屏执行体截图 | `reader/haochen_reader.py`：`screen_capture_granted()`（CGPreflightScreenCaptureAccess）+ `capture_window_image(pid)`（CGWindowListCreateImage 截目标窗口 → PNG base64；目标 pid 无 layer-0 窗口时回退截最大普通窗口，避开通知/宠物自身） | 冻结+开发 reader 实测：`--json --no-scroll` 返回 `screenshot`（736–758KB PNG） |
| P7.2 ext 喂图给视觉模型 | `app/ext/index.ts`：read_screen 读出 `data.screenshot` → `content.push({type:"image", data, mimeType:"image/png"})`（文本+截图互补）；`need_screen_recording` → details 标记 | 代码 + reader 截图实测 |
| P7.3 **pi 图像路由验证（关键风险）** | 用 vision-test 探针 + 真实 flash-vision 实测：tool-result `{type:"image"}` 被 pi 转给视觉模型，**模型准确描述测试图**（绿底、左上蓝矩形、红色大圆） | ✅ 路由通，风险消除 |
| P7.4 屏幕录制权限引导（第 2 权限） | `permissions.py`：`screen_recording_granted()`/`request_screen_recording()`；**组合引导卡片**（辅助功能 + 屏幕录制两段，各可去授权）；读屏时刻再引导；`app_shell` 未授权屏幕录制 → 气泡提示「如需看图识人，请在设置开启屏幕录制」（降级策略：文本照常，仅图片缺失） | 代码 + 权限检测（dev 环境 ax/sr 均 True） |
| P7.5 视觉 QA | 见 §二 | — |

## 二、验证证据

- **P7.3 决定性验证**：生成测试图（绿底 + 红圆 + 蓝矩）→ 最小 vision-test ext 返回 image content → 真实 deepseek-v4-flash-vision 回答：
  「绿色矩形背景 / 左上角深蓝色小矩形 / 画面中偏右较大红色正圆」——**模型看到了图** → pi 图像路由通。
- **P7.1 截图能力**：冻结 `haochen-reader --json --no-scroll` 返回 758KB PNG；开发 reader 736KB。
- **全量回归**：`tests/run_all_regressions.sh --real` → **PASS=7 FAIL=0**（L1 45/45, L2 26/26, M-C 5/5, M-E 17/17, P4 25/25, 真链路 16/16, L4 打包）。

## 三、屏幕录制权限引导（产品决策落实）

- **组合卡片**：权限不足时弹「haochen 所需权限」，分两段：① 辅助功能（读屏文本）② 屏幕录制（看图）；各有「授权」按钮直达对应系统设置；「我已授权」回查，全过即关；「暂不」降级。
- **降级策略**（产品决策 1）：屏幕录制未授权 → AX 文本照常、仅图片缺失，气泡友好提示「如需看图识人，请在设置开启屏幕录制」。
- **图片呈现**（产品决策 2）：默认文字叙述（haochen 看图后以文字答「我在图里看到了…」）；不回贴原图；「回贴原图」留作可选后续。

## 四、已知限制（如实）

1. **真机看图需双权限**：.app 需「辅助功能 + 屏幕录制」都授权才能「读屏+看图」（引导卡片闭环）。受 ad-hoc 签名影响（同前，授权不跨构建持久）——本机每构建授权一次；稳定签名则持久。
2. **开发链 TCC 归因**：开发期从终端 spawn 引擎时，读屏截图可能触发系统屏幕录制/音频授权弹窗（TCC 归因到引擎/读屏执行体而非 cmux）；生产 .app 形态由 .app 承担责任进程，引导卡片统一处理。
3. **截图尺寸**：原生窗口截图较大（~750KB），喂视觉模型 token 成本略增；如需可后续加下采样（非阻塞）。

## 五、交付物

- 代码：`reader/haochen_reader.py`（截图）、`ext/index.ts`（喂图）、`permissions.py`（屏幕录制检测/引导）、`app_shell.py`（组合引导 + 读屏再引导 + 气泡提示）
- 产物：`packaging/dist/haochen.app` / `haochen-0.1.0.dmg`（含 P7）
- 验证探针：`app/ext/vision-test.ts`（P7.3 证据）

## 六、请产品终审

研发侧 P7 已逐层验证（路由/截图/喂图/权限引导均通），真机「看图识人」在 .app 完成双权限授权后即可用。请按 `docs/signing-guide.md`（或本机授权一次）授权后，问 Chrome「图里这人是谁」验证。

—— kimi（研发）
