/** browser_control tool tests: fake `open`/`osascript` via env override; no real browser. */
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const home = mkdtempSync(join(tmpdir(), "haochen-browser-test-"));
const argfile = join(home, "argv.json");
const calls: string[] = [];

const fakeOpen = join(home, "open");
writeFileSync(
  fakeOpen,
  `#!/usr/bin/env node
const fs = require('fs');
fs.writeFileSync(${JSON.stringify(argfile)}, JSON.stringify({bin:'open', argv: process.argv.slice(2)}));
if (process.env.OPEN_CHROME_FAIL && process.argv.includes('-a')) { console.error('no chrome'); process.exit(1); }
if (process.env.OPEN_ALL_FAIL) { console.error('boom'); process.exit(1); }
`,
);
const fakeOsa = join(home, "osascript");
writeFileSync(
  fakeOsa,
  `#!/usr/bin/env node
const fs = require('fs');
const argv = process.argv.slice(2);
fs.writeFileSync(${JSON.stringify(argfile)}, JSON.stringify({bin:'osascript', argv}));
if (process.env.OSA_DENY) { console.error('45:60: execution error: Not authorized to send Apple events. (not allowed, -1743)'); process.exit(1); }
if (process.env.OSA_NOT_FOUND) { console.log('not_found'); process.exit(0); }
if (process.env.OSA_FOCUS) { console.log('focused'); process.exit(0); }
console.log('w1/1 https://github.com/settings | GitHub 设置');
console.log('w1/2 https://cowork.xiaohongshu.com | Cowork');
`,
);
chmodSync(fakeOpen, 0o700);
chmodSync(fakeOsa, 0o700);

process.env.HAOCHEN_OPEN_BIN = fakeOpen;
process.env.HAOCHEN_OSASCRIPT_BIN = fakeOsa;
process.env.HAOCHEN_BROWSER_APP = "Test Chrome";

const tools: Record<string, any> = {};
(await import("./index.ts")).default({ on() {}, registerTool(t: any) { tools[t.name] = t; } } as any);
assert.ok(tools.browser_control, "browser_control must be registered");
assert.ok(tools.browser_control.promptSnippet, "browser_control should advertise a promptSnippet");

const run = (params: any) => tools.browser_control.execute("t", params, undefined, undefined, {});
const readCall = () => JSON.parse(readFileSync(argfile, "utf8"));

// open_url：目标用户 Chrome，argv 数组无 shell
let r = await run({ action: "open_url", url: "https://github.com/settings/ssh/new" });
assert.ok(!r.isError, String(r.content[0].text));
assert.deepEqual(readCall(), { bin: "open", argv: ["-a", "Test Chrome", "https://github.com/settings/ssh/new"] });
assert.match(r.content[0].text, /Chrome/);

// open_url：拒绝非 http/https（javascript:/file:/chrome:// 一律不许）
for (const bad of ["javascript:alert(1)", "file:///etc/passwd", "chrome://extensions", "ftp://x", "  https://带空格 .com"]) {
  r = await run({ action: "open_url", url: bad });
  assert.ok(r.isError, `must reject ${bad}`);
  assert.equal(r.details.browserError, "invalid_url");
}

// open_url：Chrome 打不开时退回默认浏览器并如实说明
process.env.OPEN_CHROME_FAIL = "1";
r = await run({ action: "open_url", url: "https://example.com" });
assert.ok(!r.isError);
assert.deepEqual(readCall().argv, ["https://example.com"]);
assert.match(r.content[0].text, /默认浏览器/);
delete process.env.OPEN_CHROME_FAIL;

// open_url：全部失败 → 结构化错误，不编造成功
process.env.OPEN_ALL_FAIL = "1";
r = await run({ action: "open_url", url: "https://example.com" });
assert.ok(r.isError && r.details.browserError === "open_failed");
delete process.env.OPEN_ALL_FAIL;

// list_tabs：返回只读元数据
r = await run({ action: "list_tabs" });
assert.ok(!r.isError);
assert.match(r.content[0].text, /github\.com\/settings/);
assert.equal(readCall().argv.at(-1), "Test Chrome");

// focus_tab：命中 / 未命中 / 缺参数
process.env.OSA_FOCUS = "1";
r = await run({ action: "focus_tab", url: "github.com/settings" });
assert.ok(!r.isError && r.details.status === "focused");
assert.equal(readCall().argv.at(-1), "github.com/settings");
delete process.env.OSA_FOCUS;

process.env.OSA_NOT_FOUND = "1";
r = await run({ action: "focus_tab", url: "gitlab.com" });
assert.ok(r.isError && r.details.status === "not_found", "未命中必须如实报错，不许假装聚焦成功");
delete process.env.OSA_NOT_FOUND;

r = await run({ action: "focus_tab" });
assert.ok(r.isError && r.details.browserError === "missing_url");

// 自动化未授权：返回系统设置引导（而不是一堆原始 AppleScript 错误）
process.env.OSA_DENY = "1";
r = await run({ action: "list_tabs" });
assert.ok(r.isError && r.details.browserError === "automation_denied");
assert.match(r.content[0].text, /隐私与安全性/);
delete process.env.OSA_DENY;

console.log("[PASS] browser_control：三原语 + URL 校验 + 授权引导 + 失败诚实性");
