/** Consent/turn binding tests: synthetic metadata, no OS screen access or external calls. */
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, writeFileSync, existsSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const home = mkdtempSync(join(tmpdir(), "haochen-target-test-"));
const reader = join(home, "reader.js");
const calls = join(home, "calls.json");
writeFileSync(reader, `#!/usr/bin/env node
require('fs').writeFileSync(${JSON.stringify(calls)}, JSON.stringify(process.argv.slice(2)));
console.log(JSON.stringify({app:'A',window_title:'Page A',blocks:[{kind:'text',text:'真实内容'.repeat(50)}],stats:{text_blocks:1,images:0,images_with_url:0}}));
`);
chmodSync(reader, 0o700);
process.env.HAOCHEN_HOME = home;
process.env.HAOCHEN_PETREAD = reader;
process.env.HAOCHEN_PET = "1";
const hooks: Record<string, Function> = {};
let tool: any;
(await import("./index.ts")).default({ on(name: string, fn: Function) { hooks[name] = fn; }, registerTool(t: any) { tool = t; } } as any);
const a = { pid: 20, window_id: 40, app: "A", title: "Page A", fingerprint: "first" };
const b = { pid: 30, window_id: 50, app: "B", title: "Page B", fingerprint: "second" };
const publish = (target: any, session = "one") => writeFileSync(join(home, "question-target.json"), JSON.stringify({token:"turn",session,target}));
const begin = (prompt = "这个页面说啥") => hooks.before_agent_start({systemPrompt:"",prompt});
const execute = (confirm: Function, params = {}, signal?: AbortSignal) => tool.execute("read-this-turn", params, signal, undefined, {hasUI:true,ui:{confirm}});

publish(a);
assert.match((await begin()).systemPrompt, /还没读过/);
let confirmation = "";
const result = await execute(async (_title: string, message: string) => {
  confirmation = message;
  assert.ok(!existsSync(calls), "must not run reader before consent");
  publish(b); // User changes app while consent is pending.
  return true;
});
assert.ok(!result.isError);
assert.match(confirmation, /Page A/);
const args = JSON.parse(readFileSync(calls,"utf8"));
assert.deepEqual(JSON.parse(args[args.indexOf("--target-json") + 1]), a);
assert.ok(args.includes("--no-scroll"));
assert.ok(!args.includes("--check"), "no extra process delay between consent and capture");
assert.match((await begin()).systemPrompt, /已切换/);
const old = {role:"toolResult",toolName:"read_screen",toolCallId:"old",content:[{type:"text",text:"old page"}]};
assert.match((await hooks.context({messages:[old]})).messages[0].content[0].text, /已封存/);
await begin("继续刚才那个");
assert.equal(await hooks.context({messages:[old]}), undefined);
unlinkSync(calls);
await begin();
assert.ok((await execute(async () => false)).isError);
assert.ok(!existsSync(calls));
assert.ok((await execute(async () => true, {pid:20})).isError, "model cannot replace target PID");
assert.ok(!existsSync(calls));
const abort = new AbortController(); abort.abort();
assert.ok((await execute(async () => true, {}, abort.signal)).isError);
assert.ok(!existsSync(calls));
publish(null); await begin();
assert.ok((await execute(async () => { throw Error("must not ask about unknown target"); })).isError);
publish(a, "new-session");
assert.match((await begin()).systemPrompt, /还没读过/);
console.log("PASS: turn-bound target, consent race, no pre-consent read, rejection, abort, PID mismatch, history, unknown target and session isolation");
