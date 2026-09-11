/** Consent/turn binding tests: synthetic metadata, no OS screen access or external calls. */
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, writeFileSync, existsSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const home = mkdtempSync(join(tmpdir(), "haochen-target-test-"));
const reader = join(home, "reader.js");
const calls = join(home, "calls.json");
writeFileSync(reader, `#!/usr/bin/env node
console.log(JSON.stringify({event:'ready'}));
let target;
require('readline').createInterface({input:process.stdin}).on('line', (line) => {
const command = JSON.parse(line), id = command.id;
if (command.op === 'bind') { target = command.target; console.log(JSON.stringify({id,event:'bound'})); return; }
if (command.op === 'cancel') { target = null; return; }
if (command.op !== 'read') process.exit(1);
require('fs').writeFileSync(${JSON.stringify(calls)}, JSON.stringify({target,pid:process.pid}));
if (require('fs').existsSync(${JSON.stringify(join(home, "fail"))})) {
  console.log(JSON.stringify({id,event:'error',code:'capture_failed',message:'synthetic target closed'})); return;
}
console.log(JSON.stringify({id,event:'snapshot'}));
if (require('fs').existsSync(${JSON.stringify(join(home, "stall"))})) return;
console.log(JSON.stringify({id,event:'result',data:{app:'A',window_title:'Page A',blocks:[{kind:'text',text:'真实内容'.repeat(50)}],stats:{text_blocks:1,images:0,images_with_url:0}}}));
});
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
  assert.ok(!existsSync(calls), "must not read content before consent");
  publish(b); // User changes app while consent is pending.
  return true;
});
assert.ok(!result.isError);
assert.match(confirmation, /Page A/);
const captured = JSON.parse(readFileSync(calls,"utf8"));
assert.deepEqual(captured.target, a);
assert.match((await begin()).systemPrompt, /已切换/);
const old = {role:"toolResult",toolName:"read_screen",toolCallId:"old",content:[{type:"text",text:"old page"}]};
assert.match((await hooks.context({messages:[old]})).messages[0].content[0].text, /已封存/);
await begin("继续刚才那个");
assert.equal(await hooks.context({messages:[old]}), undefined);
unlinkSync(calls);
await begin();
assert.ok((await execute(async () => false)).isError);
assert.ok(!existsSync(calls));
const pendingAbort = new AbortController();
assert.ok((await execute(async () => { pendingAbort.abort(); return true; }, {}, pendingAbort.signal)).isError);
assert.ok(!existsSync(calls), "cancellation while consent is open must release the bound worker without reading");
assert.ok((await execute(async () => true, {pid:20})).isError, "model cannot replace target PID");
assert.ok(!existsSync(calls));
const abort = new AbortController(); abort.abort();
assert.ok((await execute(async () => true, {}, abort.signal)).isError);
assert.ok(!existsSync(calls));
publish(null); await begin();
assert.ok((await execute(async () => { throw Error("must not ask about unknown target"); })).isError);
publish(a, "new-session");
assert.match((await begin()).systemPrompt, /还没读过/);
writeFileSync(join(home,"fail"), "1");
const failure = await execute(async () => true);
assert.ok(failure.isError);
const failedMessage = {role:"toolResult",toolName:"read_screen",toolCallId:"read-this-turn",...failure};
assert.match((await hooks.context({messages:[failedMessage]})).messages[0].content[0].text, /synthetic target closed/);
assert.deepEqual(await hooks.tool_result({...failedMessage,isError:false}), {isError:true}, "pi error hook contract");
await begin();
assert.match((await hooks.context({messages:[failedMessage]})).messages[0].content[0].text, /已封存/);
assert.equal(JSON.parse(readFileSync(calls,"utf8")).pid, captured.pid, "reuse process across reads, refusal and consent cancellation");
unlinkSync(join(home, "fail"));
writeFileSync(join(home, "stall"), "1");
const {bindReader} = await import("./bound-reader.ts");
const readingAbort = new AbortController();
const held = await bindReader(reader, a, readingAbort.signal, (phase) => {
  if (phase === "snapshot") readingAbort.abort();
});
await assert.rejects(bindReader(reader, b), /已有阅读请求/);
await assert.rejects(held.read(), /取消/);
held.cancel();
unlinkSync(join(home, "stall"));
const recovered = await bindReader(reader, a);
assert.match(await recovered.read(), /真实内容/);
assert.notEqual(JSON.parse(readFileSync(calls,"utf8")).pid, captured.pid, "aborting active capture kills the worker; next request starts cleanly");
recovered.cancel();
await hooks.session_shutdown();
console.log("PASS: turn-bound target, consent race, no pre-consent read, rejection, abort, PID mismatch, history, unknown target and session isolation");
