# P7 读屏视觉增强（多模态）— 实施方案与工作量（研发 → 产品）

> 日期：2026-08-25 ｜ 作者：kimi（研发）｜ 依据：产品「视觉是早定能力（deepseek-v4-flash-vision）」+ 打回要求
> 状态：**方案待产品确认**。产品确认后纳入终审范围，实施完成再交付。

---

## 一、目标

让 haochen 读屏**能看图**——把当前窗口/屏幕的**截图**喂给视觉模型（deepseek-v4-flash-vision），
使它能识别图片里的人物、看懂图片型界面，回答时结合图片内容（不再只读 AX 文本、丢图片）。

## 二、现状与缺口

- 现状：读屏走 **AX 无障碍 API**，只取文本 + 图片的 **URL 引用**（AXImage 的 AXURL）。对「网页里一张人物照片」：
  - 若有 img src URL → 读屏执行体下载成 base64 喂给模型（能看图，但依赖 URL 存在且可下载）；
  - 若无 URL / 动态图 / canvas / 图片型 UI → **取不到像素 → 模型看不到** → 「这人是谁」读不出。
- 缺口：缺少**直接截取当前窗口/屏幕图像**的路径（`CGWindowListCreateImage`），以及配套的**屏幕录制权限**。
- 好消息：pi 引擎已支持 **ImageContent** 类型、图像输入模型（`input:["text","image"]`）；ext 的读屏工具
  已能把 `{type:"image",data,mimeType}` 塞进 tool-result content（机制已通）。**只差「截图」这个源**。

## 三、实施方案

### 1) 读屏执行体：截图当前窗口/屏幕（`haochen_reader.py` 增强）
- 新增 `capture_window(pid) -> PngData`：`CGWindowListCreateImage` 截取目标窗口 → PNG base64。
  - 目标窗口定位沿用现有 `target_pid()`（前台 App；宠物被点成前台时读它身后的用户窗口）。
  - 需要**屏幕录制权限**（`CGPreflightScreenCaptureAccess()` / `CGRequestScreenCaptureAccess()`）。
- `--json` 输出新增 `screenshot` 字段（data URL）；保留 AX 文本块（文本+视觉互补）。
- 权限未授予截图时：输出明确标记 `screenshot:null + needScreenRecording:true`（见 §四 引导）。

### 2) 引擎扩展：把截图喂给视觉模型（`app/ext/index.ts`）
- read_screen 工具：把截图（base64）作为 image content 追加到 tool-result content：
  `content.push({type:"image", data:<png b64>, mimeType:"image/png"})`（机制已有，补进截图）。
- 模型=deepseek-v4-flash-vision-exp（默认已是），图像 input 能力已具备。

### 3) 模型侧（pi 路由）
- **验证探针（关键）**：确认 pi 把 tool-result 里的 image content 真正转成给视觉模型的
  `image_url`（openai-completions 风格）。这是唯一未知点，用一张真实图片 + flash-vision 问一句验证。
- 若 pi 不转：备选 = ext 把截图以 `![图](data_url)` 内联进文字、或走 pi 的 image 专用通道（视调研结果定）。

## 四、屏幕录制权限引导（第 2 个权限，与辅助功能并行）

- 现状：已有「辅助功能」权限引导（permissions.py + 引导卡片 + 读屏时再引导）。
- 新增：**屏幕录制**权限检测与引导，**并行**于辅助功能：
  - `permissions.py` 增 `screen_recording_granted()`（`CGPreflightScreenCaptureAccess`）/ `request_screen_recording()`。
  - 引导卡片**分两节**：① 辅助功能（读屏）② 屏幕录制（读图）；或共用一张卡片两段，各自可去授权。
  - 触发时机：与读屏一致（启动首查 + 实读时刻再引导）。屏幕录制系统弹「haochen 想录制屏幕」→ 系统设置→隐私与安全性→屏幕录制→开 haochen。
  - 未授权屏幕录制：读屏**仍能用**（AX 文本照常），仅**图片部分**缺失，并在气泡提示「如需看图，请在设置开启屏幕录制」。

## 五、工作量

| 步骤 | 内容 | 预估 | 并行性 |
|---|---|---|---|
| P7.1 | 读屏执行体截图（CGWindowListCreateImage + 编码 + 定位） | 1 天 | 单 |
| P7.2 | ext 把截图作为 image content | 0.5 天 | 依赖 7.1 |
| P7.3 | **pi 图像 tool-result 路由验证探针**（风险） | 0.5–1 天 | 可与 7.1 并行 |
| P7.4 | 屏幕录制权限引导（检测 + 卡片第二段 + 读屏时再引导） | 0.5 天 | 并行 |
| P7.5 | 视觉回归（识人/看图/图片界面）+ QA | 0.5–1 天 | 依赖 7.1/7.3 |
| **合计** | | **约 3–4 天**（7.1/7.3/7.4 可并行加速自然日） | |

风险：
- **P7.3 是唯一技术不确定点**：若 pi 不支持 tool-result 图像喂视觉模型，需走备选路径（内联/专用通道），多 0.5–1 天。
- 屏幕录制权限在无头/自动化测试下无法截图（须真机授权）；视觉回归以真机人工 + 真模型为主。

## 六、交付物

- 读屏执行体截图 + ext 喂图；二维码/图片识人实测截图。
- 屏幕录制权限引导（卡片 + 读屏时刻再引导）证据。
- 视觉 QA：问 Chrome「图里这人是谁」→ 模型结合图像回答。
- 回归：L1–L4 全量 + 新增视觉场景。

请产品确认方案（尤其 ④屏幕录制权限引导的交互形态，以及 ⑦图片在回答里的呈现——短结可否带「我在图里看到了…」）。确认后纳入终审，我来实施。
