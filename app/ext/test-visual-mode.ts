/**
 * 看图模式（v0.1.8）隔离测试：假 reader（HAOCHEN_PETREAD）+ 临时 HAOCHEN_HOME，
 * 直接驱动 ext 的 read_screen.execute()（不经真模型、不经真引擎）。
 *
 * 运行（仓库根）：
 *   NODE_PATH="$PWD/pi-source/node_modules" bun run app/ext/test-visual-mode.ts
 *
 * 断言覆盖（docs/v018-todo.md 验收 + v0.1.9 损坏图防御）：
 * 1. 看图意图（提问含「帅」）+ blocks 空 + 有截图 → 结果含图像内容（visualMode）
 * 2. 稀疏文本（<80 字符）+ 有截图 → 看图模式，截图作为图像发出
 * 3. 纯文字提问 + 富文本 blocks（有原图）→ 非看图模式，原图照发、截图不发
 * 4. blocks 空 + 截图缺失 + need_screen_recording → isError + needScreenRecording
 * 5. blocks 空 + 无截图且无权限标记 → 保持旧行为「没有可读内容」
 * 6. v0.1.9：损坏图（截断 PNG，真机智谱 400 元凶）被过滤，好图照发，imagesDropped 计数
 * 7. v0.1.9：blocks 空 + 截图本身损坏 → 不上送坏图，回退「没有可读内容」
 * 退出码 0 = 全过。
 */

import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const HOME = mkdtempSync(join(tmpdir(), "haochen-visual-test-"));
const FAKE_READER = join(HOME, "fake-reader.sh");
const CASE_JSON = join(HOME, "case.json");
const LAST_TEXT = join(HOME, "last-user-text.txt");

// 1x1 透明 PNG（占位截图/原图）
const PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ" +
  "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

// 假 reader：--check 直接报已授权（退出码 0）；--json 输出当前用例 JSON
writeFileSync(
  FAKE_READER,
  '#!/bin/bash\nfor a in "$@"; do\n' +
    '  if [ "$a" = "--check" ]; then echo \'{"accessibility":true,"screen_recording":true}\'; exit 0; fi\n' +
    "done\ncat \"$FAKE_READER_JSON\"\n",
);
chmodSync(FAKE_READER, 0o755);

// env 必须在 import 扩展前设好（扩展在模块加载时读取）
process.env.HAOCHEN_HOME = HOME;
process.env.HAOCHEN_PETREAD = FAKE_READER;
process.env.FAKE_READER_JSON = CASE_JSON;

const mod = await import("./index.ts");
let tool: {
  execute: (
    id: string,
    params: Record<string, unknown>,
    signal: undefined,
    onUpdate: undefined,
    ctx: { hasUI: boolean },
  ) => Promise<{
    content: ({ type: string; text?: string; data?: string; mimeType?: string })[];
    details: Record<string, unknown>;
    isError?: boolean;
  }>;
} | null = null;
mod.default({ on() {}, registerTool(def: typeof tool) { tool = def; } });
if (!tool) {
  console.error("[FAIL] read_screen 工具未注册");
  process.exit(1);
}
const execute = tool.execute.bind(tool);

let pass = 0, fail = 0;
function check(name: string, ok: boolean, detail = ""): void {
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}` + (detail ? ` — ${detail}` : ""));
  ok ? pass++ : fail++;
}

/** 写用例：假 reader 输出 + 用户最新提问（last-user-text.txt；不传=删除意图）。 */
let lastTextWritten = false;
async function run(label: string, data: unknown, userText: string | null) {
  writeFileSync(CASE_JSON, JSON.stringify(data));
  if (userText === null) {
    try { (await import("node:fs")).unlinkSync(LAST_TEXT); } catch {}
    lastTextWritten = false;
  } else {
    writeFileSync(LAST_TEXT, userText, "utf8");
    lastTextWritten = true;
  }
  const r = await execute("t-" + label, {}, undefined, undefined, { hasUI: false });
  const texts = r.content.filter((c) => c.type === "text").map((c) => c.text ?? "");
  const images = r.content.filter((c) => c.type === "image");
  return { r, text: texts.join("\n"), images };
}

const base = { app: "微信", window_title: "对话详情",
  stats: { text_blocks: 0, images: 1, images_with_url: 0 } };

// ── 1. 看图意图 + blocks 空 + 有截图 → 发图（修复「没看到图」主路径）──
{
  const { r, text, images } = await run("看图意图",
    { ...base, blocks: [], screenshot: PNG, need_screen_recording: false },
    "这个男的帅么");
  check("看图意图: 结果含图像内容（截图发出）", images.length === 1, `images=${images.length}`);
  check("看图意图: details.visualMode=true", r.details.visualMode === true);
  check("看图意图: 不再误报「没有可读内容」", !text.includes("没有可读内容"), text.slice(0, 30));
  check("看图意图: isError 非真", !r.isError);
}

// ── 2. 稀疏文本（<80 字符）+ 有截图 → 看图模式，截图发出 ──
{
  const { r, images } = await run("稀疏文本",
    { ...base, blocks: [{ kind: "text", text: "图片" }],
      screenshot: PNG, need_screen_recording: false },
    null);
  check("稀疏文本: visualMode=true", r.details.visualMode === true);
  check("稀疏文本: 截图作为图像发出", images.length === 1, `images=${images.length}`);
  check("稀疏文本: 带截图提示语", r.content.some(
    (c) => c.type === "text" && (c.text ?? "").includes("窗口截图")));
}

// ── 3. 纯文字提问 + 富文本（有原图）→ 非看图模式，不滥用截图 ──
{
  const longText = "这是一段足够长的正文，用来模拟真实可读的文章窗口。".repeat(5);
  const { r, images } = await run("纯文字",
    { ...base, window_title: "技术文章",
      stats: { text_blocks: 1, images: 1, images_with_url: 1 },
      blocks: [{ kind: "text", text: longText },
               { kind: "image", url: "https://x/a.png", alt: "配图", data_url: PNG, area: 100 }],
      screenshot: PNG, need_screen_recording: false },
    "总结这段文字");
  check("纯文字: visualMode 未标记", !r.details.visualMode);
  check("纯文字: 只有原图一张、截图不发", images.length === 1 && !r.content.some(
    (c) => c.type === "text" && (c.text ?? "").includes("窗口截图")), `images=${images.length}`);
}

// ── 4. blocks 空 + 截图缺失 + need_screen_recording → 明确报错 ──
{
  const { r, text } = await run("无权限",
    { ...base, blocks: [], screenshot: null, need_screen_recording: true },
    "这张图里是谁");
  check("无权限: isError=true", r.isError === true);
  check("无权限: details.needScreenRecording", r.details.needScreenRecording === true);
  check("无权限: 文案提及屏幕录制权限", text.includes("屏幕录制"), text.slice(0, 30));
}

// ── 5. blocks 空 + 无截图且无权限标记 → 旧行为「没有可读内容」──
{
  const { r, text, images } = await run("空窗口",
    { ...base, app: "代码编辑器", blocks: [], screenshot: null, need_screen_recording: false },
    null);
  check("空窗口: 保持旧「没有可读内容」文案", text.includes("没有可读内容"), text.slice(0, 30));
  check("空窗口: 无图像内容", images.length === 0);
  check("空窗口: isError 非真", !r.isError);
}

// ── 6. v0.1.9：损坏图（截断 PNG）被过滤，好图照发 ──
// 真机案例：小红书窗口下载到 78 字节截断 PNG（有头无 IEND），原样上送
// 导致智谱 400「messages[4].image[0]: unsupported image」。
{
  // 真机截获的截断 PNG（104 字符 base64，无 IEND 结束块）
  const CORRUPT_PNG =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAM0AAABgCAYAAAC+PvZZAAAACXBIWXMAACE4AAAhOAFFljFgAAAAAXNSR0IArs4c6QAAAARnQUrkJggg";
  const longText = "这是一段足够长的正文，用来模拟真实可读的文章窗口。".repeat(5);
  const { r, images } = await run("损坏图过滤",
    { ...base, window_title: "技术文章",
      stats: { text_blocks: 1, images: 2, images_with_url: 2 },
      blocks: [{ kind: "text", text: longText },
               { kind: "image", url: "https://x/broken.png", alt: "坏图", data_url: CORRUPT_PNG, area: 90000 },
               { kind: "image", url: "https://x/ok.png", alt: "好图", data_url: PNG, area: 80000 }],
      screenshot: PNG, need_screen_recording: false },
    "总结这段文字");
  check("损坏图: 截断 PNG 被过滤，只有好图发出",
    images.length === 1 && images[0].mimeType === "image/png", `images=${images.length}`);
  check("损坏图: details.imagesDropped=1", r.details.imagesDropped === 1);
  check("损坏图: 上送图像是纯 base64 且 PNG 完整可解码",
    /^[A-Za-z0-9+/]+={0,2}$/.test(images[0].data ?? "") &&
    Buffer.from(images[0].data ?? "", "base64").subarray(0, 4).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47])));
}

// ── 7. v0.1.9：blocks 空 + 截图本身损坏 → 不上送坏图，回退旧文案 ──
{
  const CORRUPT_PNG =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAM0AAABgCAYAAAC+PvZZAAAACXBIWXMAACE4AAAhOAFFljFgAAAAAXNSR0IArs4c6QAAAARnQUrkJggg";
  const { r, text, images } = await run("截图损坏",
    { ...base, blocks: [], screenshot: CORRUPT_PNG, need_screen_recording: false },
    "这张图里是谁");
  check("截图损坏: 无图像内容上送", images.length === 0, `images=${images.length}`);
  check("截图损坏: 不标记 screenshotAttached", !r.details.screenshotAttached);
  check("截图损坏: 回退「没有可读内容」文案", text.includes("没有可读内容"), text.slice(0, 30));
}

console.log(`\n==== ${pass}/${pass + fail} 通过 ====`);
process.exit(fail ? 1 : 0);
