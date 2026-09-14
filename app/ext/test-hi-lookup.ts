/** hi_lookup tool tests: fake `hi` via HAOCHEN_HI, no real Hi/network access. */
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const home = mkdtempSync(join(tmpdir(), "haochen-hi-test-"));
const hi = join(home, "hi.js");
const argfile = join(home, "argv.json");
writeFileSync(
  hi,
  `#!/usr/bin/env node
const fs = require('fs'); const argv = process.argv.slice(2);
fs.writeFileSync(${JSON.stringify(argfile)}, JSON.stringify(argv));
if (process.env.HI_FAIL) { console.error('boom'); process.exit(1); }
const cmd = argv[0];
if (cmd === 'search:message') console.log(JSON.stringify({items:[{senderName:'李四',content:'@时田 看下'}]}));
else if (cmd === 'todos:list-tasks') console.log(JSON.stringify({taskList:[{title:'处理X',taskStatus:'1'}]}));
else console.log(JSON.stringify([{scheduleList:[]}]));
`,
);
chmodSync(hi, 0o700);
process.env.HAOCHEN_HI = hi;

const tools: Record<string, any> = {};
(await import("./index.ts")).default({ on() {}, registerTool(t: any) { tools[t.name] = t; } } as any);
assert.ok(tools.hi_lookup, "hi_lookup must be registered");
assert.ok(tools.hi_lookup.promptSnippet, "hi_lookup should advertise a promptSnippet");

const run = (params: any) => tools.hi_lookup.execute("t", params, undefined, undefined, {});
const readArgv = () => JSON.parse(readFileSync(argfile, "utf8"));

// list_todos -> fixed argv, real-ish output surfaced to the model.
let r = await run({ action: "list_todos" });
assert.ok(!r.isError);
assert.deepEqual(readArgv(), ["todos:list-tasks", "--task-scenario", "responsible"]);
assert.match(r.content[0].text, /处理X/);

// search_message requires a query.
r = await run({ action: "search_message" });
assert.ok(r.isError, "search_message without query must error");

// search_message passes query + bounded page-size via argv array (no shell).
r = await run({ action: "search_message", query: "李四", limit: 5 });
assert.ok(!r.isError);
assert.deepEqual(readArgv(), ["search:message", "--query", "李四", "--page-size", "5"]);
assert.match(r.content[0].text, /李四/);

// limit is clamped to <= 50.
await run({ action: "search_message", query: "x", limit: 999 });
assert.equal(readArgv()[4], "50");

// get_schedules builds a begin/end window.
r = await run({ action: "get_schedules" });
assert.ok(!r.isError);
const s = readArgv();
assert.equal(s[0], "calendar:get-user-schedules");
assert.ok(s.includes("--begin-time") && s.includes("--end-time"));

// failure path surfaces isError, never throws.
process.env.HI_FAIL = "1";
r = await run({ action: "list_todos" });
assert.ok(r.isError && /失败/.test(r.content[0].text));
delete process.env.HI_FAIL;

console.log("[PASS] hi_lookup tool");
