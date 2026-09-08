/**
 * haochen — 集成 App 引擎扩展（P4）
 *
 * 由 App 壳以 `-e app/ext/index.ts` 显式加载（--no-extensions 禁用自动发现）。
 * 提供：
 * - read_screen 工具：Accessibility 读屏（文本/原图为主；图片型窗口附窗口截图，
 *   v0.1.8 看图模式），宠物内强制先确认（契约 §5.2）
 * - 单回合分层输出：HAOCHEN_PET=1（壳 spawn 时注入，engine_client.spawn_argv）时
 *   注入 RESULT_RULES，一次生成可直接显示的 brief 与可展开的 detail（契约 §4）。
 *
 * 源自 previous-version/haochen-app/ext/index.ts（P1 已验证 Bun 直载），
 * P4 适配：去掉旧 /pet 命令（旧 bridge 已废）；读屏签名/工具路径支持
 * HAOCHEN_HOME / HAOCHEN_PETREAD 环境变量覆盖（测试隔离）。
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";
import { execFile } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";
import { chmodSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";

/** 分层结果协议只在 App 内强制：壳 spawn 引擎时注入（engine_client.spawn_argv）。 */
const IN_PET = process.env.HAOCHEN_PET === "1";

/** 数据目录：与壳一致（契约 §1.1），默认 ~/Library/Application Support/haochen。 */
const HAOCHEN_HOME =
  process.env.HAOCHEN_HOME || join(homedir(), "Library", "Application Support", "haochen");

// 最后一次成功读屏的签名（pid + 窗口标题）——供后续「页面是否变化」检测（P6 接）。
const LAST_READ_SIG = join(HAOCHEN_HOME, "last-read-sig.json");

/** v0.1.7：用户称呼档案（壳侧 pet/app.py 首启询问后落盘）。读失败一律静默。 */
function loadUserName(): string {
  try {
    const raw = readFileSync(join(HAOCHEN_HOME, "user-profile.json"), "utf8");
    const name = (JSON.parse(raw) as { name?: unknown }).name;
    return typeof name === "string" ? name.trim() : "";
  } catch {
    return "";
  }
}

/** Privacy boundary: the Python shell persists only a derived boolean, never the prompt. */
function loadLastUserVisualIntent(): boolean {
  try {
    const raw = readFileSync(join(HAOCHEN_HOME, "last-user-intent.json"), "utf8");
    return (JSON.parse(raw) as { visual?: unknown }).visual === true;
  } catch {
    return false;
  }
}

function ensurePrivateHome() {
  mkdirSync(HAOCHEN_HOME, { recursive: true, mode: 0o700 });
  chmodSync(HAOCHEN_HOME, 0o700);
}

function writeLastReadSig(pid: number | null, title: string) {
  try {
    ensurePrivateHome();
    writeFileSync(
      LAST_READ_SIG,
      JSON.stringify({ pid: pid ?? null, title, time: Date.now() }),
      { encoding: "utf8", mode: 0o600 },
    );
    chmodSync(LAST_READ_SIG, 0o600);
  } catch {}
}

function clearLastReadSig() {
  try {
    unlinkSync(LAST_READ_SIG);
  } catch {}
}

/** 读屏执行体（Accessibility 读取，非截图）。默认 ~/.local/bin/haochen --json；
 *  P5 打包时内嵌读取器并用 HAOCHEN_PETREAD 指向 Resources。 */
const PETREAD = process.env.HAOCHEN_PETREAD || join(homedir(), ".local", "bin", "haochen");
const MAX_TEXT_CHARS = 40_000;
const MAX_IMAGES = 15;
const READ_TIMEOUT_MS = 120_000;

// ── 看图模式（v0.1.8）────────────────────────────────────────
// 图片在 AX 树里只有占位、无文字 → 纯 AX 读屏拿不到画面（真机：微信图片窗口
// 问「这个男的帅么」被答"没看到图"）。满足任一条件即判定看图模式，把窗口截图
// 作为主输入发给多模态模型：
/** ① 用户意图由壳侧计算为隐私安全的 boolean sidecar。 */
/** ② 窗口类型：app 名 / 窗口标题命中图片/视频/预览类。 */
const VISUAL_WINDOW_WORDS = [
  "图片", "照片", "视频", "预览", "图像", "image", "photo", "video", "preview", "quick look",
];
/** ③ AX 文本总量低于该长度视为「只有标题没正文」（图片型/自绘界面）。 */
const SPARSE_TEXT_CHARS = 80;

function detectVisualMode(data: PetreadJson): boolean {
  if (loadLastUserVisualIntent()) return true;
  const win = `${data.app} ${data.window_title}`.toLowerCase();
  if (VISUAL_WINDOW_WORDS.some((w) => win.includes(w))) return true;
  if (!data.blocks.length) return true;
  const len = data.blocks
    .filter((b) => b.kind === "text")
    .reduce((n, b) => n + (b.text ?? "").length, 0);
  return len < SPARSE_TEXT_CHARS;
}

/** 解析并校验 data URL 截图/图片 → { data, mime }；非法/损坏返回 null。
 *  v0.1.9 防御：真机出现过 reader 下载到截断 PNG（只有头没有 IDAT/IEND），
 *  原样上送导致智谱 400「messages[4].image[0]: unsupported image」整轮失败。
 *  发送前校验：纯 base64 可解码 + magic 头匹配 mime + 结构完整（PNG 须有 IEND、
 *  JPEG 须 FFD8…FFD9、WebP 的 RIFF 声明长度须与实际一致、GIF 头）。 */
function parseDataImage(u?: string | null): { data: string; mime: string } | null {
  if (!u) return null;
  const m = /^data:(image\/[a-z]+);base64,(.+)$/s.exec(u);
  if (!m) return null;
  const mime = m[1];
  // 纯 base64：剔除一切空白后必须严格符合字符集与 4 字节对齐
  const data = m[2].replace(/\s+/g, "");
  if (!data || data.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(data)) return null;
  let buf: Buffer;
  try {
    buf = Buffer.from(data, "base64");
  } catch {
    return null;
  }
  if (buf.length < 8) return null;
  if (mime === "image/png") {
    // 签名 + 必须以完整 IEND 块收尾（截断 PNG 的判定依据）
    const PNG_SIG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    const IEND = [0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82];
    if (!PNG_SIG.every((b, i) => buf[i] === b)) return null;
    if (buf.length < 20 || !IEND.every((b, i) => buf[buf.length - IEND.length + i] === b)) return null;
  } else if (mime === "image/jpeg") {
    if (buf[0] !== 0xff || buf[1] !== 0xd8) return null;
    if (buf[buf.length - 2] !== 0xff || buf[buf.length - 1] !== 0xd9) return null;
  } else if (mime === "image/gif") {
    const head = buf.subarray(0, 6).toString("ascii");
    if (head !== "GIF87a" && head !== "GIF89a") return null;
  } else if (mime === "image/webp") {
    if (buf.length < 12) return null;
    if (buf.subarray(0, 4).toString("ascii") !== "RIFF") return null;
    if (buf.subarray(8, 12).toString("ascii") !== "WEBP") return null;
    if (buf.readUInt32LE(4) !== buf.length - 8) return null; // RIFF 长度须与实际一致
  } else {
    return null; // 仅放行 png/jpeg/gif/webp（主流视觉端点支持集）
  }
  return { data, mime };
}

const RESULT_RULES = `
# haochen 输出协议 · 单回合结果（强制）

一次回答同时服务桌面简答与完整详情。你必须遵守：

1. 可以先调用工具（如 read_screen、bash），但最终正文必须严格输出两个配对块，顺序不可颠倒：
   【brief】
   …一句结论 + 最多两个高信息量要点…
   【/brief】
   【detail】
   …完整回答…
   【/detail】
2. **brief** 必须能独立回答用户：第一句直接给判断或结果，全文通常 24～120 个中文字符；
   最多两个要点，不写过程复述、客套、元评论或“详见下文”。
3. **detail** 承载依据、步骤、限制和产物；结论仍放最前，不能复制粘贴 brief 来凑内容。
4. **禁止**输出 answer/summary 旧标记或交叉错标；闭合标记只能用【/brief】和【/detail】，
   禁止用等号写法替代【】系标记。
5. detail 内用简短 Markdown；==文字== 仅限正文内**关键词**高亮、少量使用，
   ==禁止==用于分节标题或协议标记；禁止表格/流程图/ASCII 图。
6. 不要客套开场；不要复述工具过程；不确定要说明。
7. **口吻**：你是住在用户桌面上的像素眼镜小哥，是伙伴，不是客服。
   - 闲聊、打招呼、问候时：像朋友一样自然亲切、口语化，一两句就够；
     ==禁止==机械列点，==禁止==「有什么可以帮您」「很高兴为您服务」式客服腔。
   - 技术问题：保持专业、直接、有用的「有帮助的技术专家」风格。
8. **读屏策略**（严格按用户消息前缀里的「页面状态」信号行事）：
   - 问题不依赖当前屏幕内容 → 直接答，不要读屏。
   - 「页面状态：相同」→ 直接沿用上次读到的内容答，不要重复读。
   - 「页面状态：已切换」→ 上次读到的内容==已作废==：依赖屏幕的问题必须调用 read_screen 重新读（会先弹确认）；**严禁**用旧页面内容回答当前页面，也禁止混用。
   - 「还没读过屏」→ 依赖屏幕的问题必须调用 read_screen（会先弹确认，用户同意才读）。
   - 用户拒绝读屏 → 明说「没读屏，以下基于已有信息」再作答。
`.trim();

interface PetreadBlock {
  kind: "text" | "image";
  text?: string;
  url?: string | null;
  alt?: string;
  data_url?: string;
  area?: number;   // 可见面积（尺寸优先级用）
  w?: number;
  h?: number;
}

interface PetreadJson {
  app: string;
  window_title: string;
  stats: { text_blocks: number; images: number; images_with_url: number };
  blocks: PetreadBlock[];
  screenshot?: string | null;           // 窗口截图像 data URL（P7 视觉）
  need_screen_recording?: boolean;      // 未授权屏幕录制（截图缺失）
}

function runPetread(args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(
      PETREAD,
      args,
      { timeout: READ_TIMEOUT_MS, maxBuffer: 64 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err) {
          reject(new Error(`读屏执行体失败: ${err.message}\n${stderr}`));
        } else {
          resolve(stdout);
        }
      },
    );
  });
}

/** 读屏执行体权限自查（haochen-reader --check，退出码 0=已授权, 2=未授权）。
 *  "unknown"：执行体缺失/超时/不识别 --check（旧开发期脚本）→ 放行给真实读取自行报错。 */
function checkReadPermission(): Promise<"granted" | "denied" | "unknown"> {
  return new Promise((resolve) => {
    execFile(PETREAD, ["--check"], { timeout: 15_000 }, (err) => {
      if (!err) return resolve("granted");
      const code = (err as { code?: number | string }).code;
      resolve(code === 2 ? "denied" : "unknown");
    });
  });
}

export default function (pi: ExtensionAPI) {
  pi.on("before_agent_start", async (event) => {
    if (!IN_PET) return;
    // 普通回合：单回合 brief + detail（+ 已知用户称呼，闲聊时可自然称呼）
    const name = loadUserName();
    const nameHint = name
      ? `\n\n# 用户称呼\n用户希望被称呼为「${name}」。闲聊问候时可以自然地这么称呼 TA（别生硬嵌入）；技术回答不必刻意带称呼。`
      : "";
    return {
      systemPrompt: `${event.systemPrompt}\n\n${RESULT_RULES}${nameHint}`,
    };
  });

  pi.registerTool({
    name: "read_screen",
    label: "Read Screen",
    description:
      "读取用户当前活跃窗口的真实内容（文本 + 图片），通过 macOS 无障碍 API 获取，" +
      "不是截图。适合用户让你“看看当前窗口/这个页面/屏幕上的内容”时使用。" +
      "返回窗口来源、正文文本和页面中的图片（图片以图像内容直接给出）。" +
      "图片/视频等读不到文字的窗口会自动附窗口截图，可据截图直接看图回答。",
    parameters: Type.Object({
      scroll: Type.Optional(
        Type.Boolean({ description: "是否自动向下滚动读取整页（默认 true）" }),
      ),
      max_scrolls: Type.Optional(
        Type.Number({ description: "最多滚动屏数（默认 20）" }),
      ),
      pid: Type.Optional(
        Type.Number({
          description:
            "锁定读取指定进程（pid）的窗口，不传则读当前前台窗口。" +
            "用户中途切走窗口时，用这个参数读回当时询问的窗口。",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, _ctx) {
      // App 内：读屏是敏感操作，一律先请用户确认（确认条在气泡/对话窗口里）。
      // 权限未授权时由 App 侧再引导（app_shell 监听 read_screen 工具调用触发）。
      // 确认通过后先跑执行体 --check 前置校验（执行体是我们自己的，支持 --check），
      // 未授权直接报错引导，绝不进入真实读取；执行体侧也有硬门控兜底。
      if (IN_PET && _ctx.hasUI) {
        const ok = await _ctx.ui.confirm(
          "haochen 想读屏",
          "需要读取当前活跃窗口的屏幕内容才能回答。点「读吧」允许；点「不读」拒绝。",
          { timeout: 120_000 },
        );
        if (!ok) {
          if (IN_PET) clearLastReadSig();
          return {
            content: [
              {
                type: "text" as const,
                text: "用户拒绝了读屏请求（或超时未确认）。请基于已有信息回答，并明确说明没有读屏。",
              },
            ],
            details: {},
            isError: true,
          };
        }
      }

      // 前置权限校验（v0.1.4 hotfix）：未授权 → 明确报错 + permissionDenied 标记。
      const perm = await checkReadPermission();
      if (perm === "denied") {
        if (IN_PET) clearLastReadSig();
        return {
          content: [
            {
              type: "text" as const,
              text:
                "屏幕读取权限未授权：请在系统设置授予 haochen「辅助功能」权限" +
                "（首次会弹系统授权框），授权后重试。",
            },
          ],
          details: { permissionDenied: true },
          isError: true,
        };
      }

      const args = ["--json"];
      if (typeof params.pid === "number") {
        args.push("--pid", String(params.pid));
      }
      if (params.scroll === false) {
        args.push("--no-scroll");
      } else {
        args.push("--max-scrolls", String(params.max_scrolls ?? 20));
      }

      let data: PetreadJson;
      try {
        const stdout = await runPetread(args);
        data = JSON.parse(stdout) as PetreadJson;
      } catch (e) {
        if (IN_PET) clearLastReadSig();
        return {
          content: [{ type: "text" as const, text: `读屏失败：${(e as Error).message}` }],
          details: {},
          isError: true,
        };
      }

      // 看图模式判定（v0.1.8）：意图 / 窗口类型 / AX 空或文本极少，任一命中
      const visual = detectVisualMode(data);

      if (!data.blocks.length) {
        // v0.1.8 修复：AX 空 ≠ 没画面（图片在 AX 树只有占位）。截图在就把截图
        // 发给模型，不再误报「没有可读内容」。
        const shot = parseDataImage(data.screenshot);
        if (shot) {
          if (IN_PET) {
            writeLastReadSig(
              typeof params.pid === "number" ? params.pid : null,
              data.window_title || "",
            );
          }
          return {
            content: [
              {
                type: "text" as const,
                text:
                  `窗口来源：[${data.app}] ${data.window_title}\n` +
                  "该窗口没有可读文本（图片/自绘界面），已截取窗口画面，请据图回答。\n",
              },
              { type: "image" as const, data: shot.data, mimeType: shot.mime },
            ],
            details: {
              app: data.app,
              title: data.window_title,
              visualMode: true,
              imagesAttached: 0,
              screenshotAttached: true,
            },
          };
        }
        if (IN_PET) clearLastReadSig();
        if (data.need_screen_recording) {
          // 看图要截图，但屏幕录制未授权 → 明确报错（壳层现有引导接管）
          return {
            content: [
              {
                type: "text" as const,
                text:
                  "这个窗口没有可读文本（图片/自绘界面），需要「屏幕录制」权限才能截图看图。" +
                  "请在系统设置授予 haochen 屏幕录制权限后重试。",
              },
            ],
            details: {
              app: data.app,
              title: data.window_title,
              visualMode: true,
              needScreenRecording: true,
            },
            isError: true,
          };
        }
        return {
          content: [
            {
              type: "text" as const,
              text: `[${data.app}] ${data.window_title}\n该窗口没有可读内容（可能是自绘界面）。`,
            },
          ],
          details: { app: data.app, title: data.window_title, visualMode: true },
        };
      }

      // 读屏成功：记录签名（pid + 窗口标题），供「页面是否变化」检测（P6 接线）
      if (IN_PET) {
        writeLastReadSig(
          typeof params.pid === "number" ? params.pid : null,
          data.window_title || "",
        );
      }

      const texts = data.blocks.filter((b) => b.kind === "text").map((b) => b.text ?? "");
      let body = texts.join("\n");
      let truncated = false;
      if (body.length > MAX_TEXT_CHARS) {
        body = body.slice(0, MAX_TEXT_CHARS);
        truncated = true;
      }

      const header =
        `窗口来源：[${data.app}] ${data.window_title}\n` +
        `（共 ${data.stats.text_blocks} 段文本、${data.stats.images} 张图片` +
        `${truncated ? "，文本已截断" : ""}）\n\n`;

      const content: ({ type: "text"; text: string } | { type: "image"; data: string; mimeType: string })[] = [];

      // 口图策略（P7 优化）：优先喂「图片原图 URL 下载」最准（识人/识界面）；
      // 按可见面积从大到小优先（大而显眼的人物图先喂）；无 URL 原图时才用截图兜底。
      // v0.1.9：原图逐张过 parseDataImage 校验，损坏图（截断/伪格式）跳过不上送——
      // 否则视觉端点在坏图上整请求 400（真机：智谱 messages[4].image[0]）。
      let nImg = 0;
      let nDropped = 0;
      const imgs = data.blocks
        .filter((b) => b.kind === "image" && b.data_url)
        .sort((a, b) => (b.area || 0) - (a.area || 0));
      for (const b of imgs) {
        if (nImg >= MAX_IMAGES) break;
        const m = parseDataImage(b.data_url);
        if (!m) {
          nDropped += 1;
          continue;
        }
        nImg += 1;
        content.push({ type: "text", text: `\n[图片 ${nImg}: ${b.alt || "无描述"}]` });
        content.push({ type: "image", data: m.data, mimeType: m.mime });
      }

      // 文本内容作为背景（图片之后，供模型结合）
      if (body) {
        content.push({ type: "text", text: header + body });
      }

      // 截图：看图模式下截图就是主输入（有就发，不再要求无原图）；
      // 非看图模式保持原兜底——仅当无 URL 原图可下载（动态图/canvas/图片型UI）才发
      const shot = parseDataImage(data.screenshot);
      if (shot && (visual || nImg === 0)) {
        content.push({ type: "text", text: "\n[窗口截图（无法提取图片原图，整窗兜底）：请据此看图]。" });
        content.push({ type: "image", data: shot.data, mimeType: shot.mime });
      }

      const details: Record<string, unknown> = {
        app: data.app,
        title: data.window_title,
        stats: data.stats,
        imagesAttached: nImg,
        truncated,
      };
      if (nDropped > 0) {
        details.imagesDropped = nDropped;  // v0.1.9：被校验过滤的损坏图数量
      }
      if (visual) {
        details.visualMode = true;  // v0.1.8 看图模式标记（回归断言/壳层提示用）
      }
      if (shot && (visual || nImg === 0)) {
        details.screenshotAttached = true;
      }
      if (data.need_screen_recording) {
        details.needScreenRecording = true;
      }

      return {
        content,
        details,
      };
    },
  });
}
