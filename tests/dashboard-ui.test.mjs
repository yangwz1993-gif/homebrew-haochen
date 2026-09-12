import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const require = createRequire(import.meta.url);
const ui = require('../app/assets/dashboard/dashboard.js');
const asset = name => readFileSync(fileURLToPath(new URL(`../app/assets/dashboard/${name}`,import.meta.url)),'utf8');
const draft = overrides => ({title:'发布准备',goal:'确认反馈中的问题是否解决',frequency:'hourly',aiEnabled:false,sources:[{type:'url',label:'公开反馈',locator:'https://example.com/feedback'}],...overrides});

test('empty state contains no demonstration data and safe appearance defaults',() => {
  const state = ui.normalizeState(null);
  for (const field of ['events','tracks','files','connectors','calendar','reports']) assert.deepEqual(state[field],[]);
  assert.equal(state.settings.palette,'sage'); assert.equal(state.settings.dock,'side'); assert.equal(state.settings.motion,'system');
  assert.equal(state.updatedAt,'');
});

test('unknown settings and malformed collections never become valid connections',() => {
  const state = ui.normalizeState({events:[null,'string',{id:'1'}],tracks:'bad',connectors:{status:'connected'},settings:{palette:'fake',dock:'fullscreen',motion:'always'}});
  assert.deepEqual(state.events,[{id:'1'}]); assert.deepEqual(state.tracks,[]); assert.deepEqual(state.connectors,[]);
  assert.equal(state.settings.palette,'sage'); assert.equal(ui.statusLabel('made_up'),'状态未知');
  assert.equal(ui.statusLabel('processing'),'处理中'); assert.equal(ui.statusLabel('awaiting'),'等待确认');
});

test('normalizer bounds high volume collections without inventing entries',() => {
  const state = ui.normalizeState({events:Array.from({length:1200},(_,id)=>({id})),tracks:Array.from({length:201},(_,id)=>({id}))});
  assert.equal(state.events.length,1000); assert.equal(state.tracks.length,200);
});

test('external URLs cannot execute scripts, carry credentials, or expose local paths',() => {
  for (const url of ['javascript:alert(1)','data:text/html,<script>x</script>','file:///private/file','otty:focus','https://user:password@example.com','https://user@example.com','not a URL','']) assert.equal(ui.safeURL(url),null,url);
  assert.equal(ui.safeURL('https://example.com/page?q=1'),'https://example.com/page?q=1');
  assert.equal(ui.safeURL('http://example.com'),'http://example.com/');
});

test('tracking validates limits, a goal and explicit AI opt in',() => {
  assert.ok(ui.validateTrackDraft(draft({title:''})).error);
  assert.ok(ui.validateTrackDraft(draft({title:'x'.repeat(121)})).error);
  assert.ok(ui.validateTrackDraft(draft({goal:''})).error);
  assert.ok(ui.validateTrackDraft(draft({goal:'x'.repeat(3001)})).error);
  assert.ok(ui.validateTrackDraft(draft({frequency:'every-second'})).error);
  assert.ok(ui.validateTrackDraft(draft({sources:[]})).error);
  assert.ok(ui.validateTrackDraft(draft({sources:Array(13).fill({type:'url',locator:'https://example.com'})})).error);
  assert.equal(ui.validateTrackDraft(draft({aiEnabled:'true'})).value.aiEnabled,false);
  assert.equal(ui.validateTrackDraft(draft({aiEnabled:true})).value.aiEnabled,true);
});

test('tracking source contracts accept actual selected references and reject credential URLs',() => {
  const value = ui.validateTrackDraft(draft({id:'track-1',sources:[{id:'source-1',type:'file',label:'本地反馈',locator:'/Users/example/Documents/feedback.md'},{type:'connector',label:'Agent 状态',locator:'event-id-1'}]})).value;
  assert.equal(value.id,'track-1'); assert.equal(value.sources[0].id,'source-1'); assert.equal(value.sources.length,2);
  assert.ok(ui.validateTrackDraft(draft({sources:[{type:'file',locator:'relative/path'}]})).error);
  assert.ok(ui.validateTrackDraft(draft({sources:[{type:'url',locator:'https://key:secret@example.com'}]})).error);
  assert.ok(ui.validateTrackDraft(draft({sources:[{type:'shell',locator:'arbitrary'}]})).error);
  assert.ok(ui.validateTrackDraft(draft({sources:[{type:'url',locator:'https://example.com/'+ 'x'.repeat(2048)}]})).error);
});

test('track text remains literal data, not markup',() => {
  const title = '<img src=x onerror=alert(1)>';
  assert.equal(ui.validateTrackDraft(draft({title})).value.title,title);
  const source = asset('dashboard.js');
  assert.doesNotMatch(source,/\.innerHTML\s*=|insertAdjacentHTML|document\.write\s*\(|\beval\s*\(|new Function\s*\(/);
  assert.match(source,/el\.textContent = text\(copy\)/);
});

test('date handling uses local dates and rejects rollover dates',() => {
  assert.equal(ui.localDate(new Date(2026,8,13,0,1)),'2026-09-13');
  assert.equal(ui.validDate('2026-02-30'),false); assert.equal(ui.validDate('2024-02-29'),true);
  assert.equal(ui.validDate(undefined),false); assert.equal(ui.validDate('2026-9-1'),false);
});

test('floating geometry remains inside a bounded native window',() => {
  for (const [width,height] of [[860,680],[540,480],[320,320],[250,200]]) {
    const box = ui.floatingGeometry(width,height,700,2000);
    assert.ok(box.left>=0); assert.ok(box.top>=0); assert.ok(box.left+box.width<=width); assert.ok(box.top+box.height<=height);
    assert.ok(box.height<=height-34); assert.ok(box.width<=width-36);
  }
});

test('Esc does not consume a Chinese IME candidate or immediate composition ending',() => {
  assert.equal(ui.mayEscape(true,0,1000),false);
  assert.equal(ui.mayEscape(false,950,1000),false);
  assert.equal(ui.mayEscape(false,900,1000),true);
});

test('bridge correlates requests and applies only actual native state',async () => {
  const sent=[], states=[];
  const bridge=new ui.NativeBridge(message=>sent.push(message),{onState:state=>states.push(state)});
  const pending=bridge.request('trackPause',{id:'track-1',paused:true});
  assert.equal(sent[0].v,1); assert.equal(sent[0].action,'trackPause'); assert.equal(typeof sent[0].id,'string');
  bridge.receive({id:'unknown',ok:true}); assert.equal(bridge.pending.size,1);
  bridge.receive({id:sent[0].id,ok:true,state:{tracks:[{id:'track-1',paused:true}]}});
  await pending; assert.equal(bridge.pending.size,0); assert.equal(states.length,1); bridge.destroy();
});

test('bridge is race-safe when native replies synchronously',async () => {
  let bridge;
  bridge=new ui.NativeBridge(message=>bridge.receive({id:message.id,ok:true}));
  await bridge.request('ready'); assert.equal(bridge.pending.size,0); bridge.destroy();
});

test('initial state push completes only the ready handshake, never a pending write',async () => {
  const bridge=new ui.NativeBridge(()=>{});
  const ready=bridge.request('ready'), mutation=bridge.request('trackPause',{id:'1',paused:true});
  bridge.receive({state:{tracks:[]}}); await ready;
  assert.equal(bridge.pending.size,1); assert.equal([...bridge.pending.values()][0].action,'trackPause');
  bridge.destroy(); await assert.rejects(mutation,/已关闭/);
});

test('bridge does not report a failed action as successful',async () => {
  let sent;
  const bridge=new ui.NativeBridge(message=>{sent=message;});
  const pending=bridge.request('connectorEnable',{id:'calendar',enabled:true});
  bridge.receive({id:sent.id,ok:false,error:'尚未取得授权'});
  await assert.rejects(pending,/尚未取得授权/); assert.equal(bridge.pending.size,0); bridge.destroy();
});

test('missing backend is explicit and unknown actions are never sent',async () => {
  let sends=0;
  const bridge=new ui.NativeBridge(()=>{sends++;throw new Error('missing');});
  await assert.rejects(bridge.request('runShell',{command:'invalid'}),/不支持/); assert.equal(sends,0);
  await assert.rejects(bridge.request('ready'),/无法连接/); assert.equal(bridge.pending.size,0); bridge.destroy();
});

test('timeout and shutdown release every pending request',async () => {
  const bridge=new ui.NativeBridge(()=>{},{timeout:5});
  const keepAlive=setTimeout(()=>{},100);
  await assert.rejects(bridge.request('refresh'),/暂未响应/);
  assert.equal(bridge.pending.size,0);
  const pending=bridge.request('ready'); bridge.destroy(); await assert.rejects(pending,/已关闭/); clearTimeout(keepAlive);
  await assert.rejects(bridge.request('ready'),/已关闭/);
});

test('production UI has bundled resources, CSP, visible daily report and no fake desktop',() => {
  const html=asset('index.html'), js=asset('dashboard.js');
  assert.match(html,/Content-Security-Policy/); assert.match(html,/connect-src 'none'/);
  assert.match(html,/src="icons\.js"/); assert.match(html,/src="dashboard\.js"/); assert.match(html,/今日日报/);
  assert.doesNotMatch(html,/<script[^>]+src="https?:/); assert.doesNotMatch(js,/globalThis\.Tweak|\.createIcons\(|fetch\s*\(/);
  assert.doesNotMatch(html,/hl-desktop|hl-pet|演示数据|示例消息|data:image\/png;base64/);
  assert.match(js,/overview\.inert = true/); assert.match(js,/compositionstart/); assert.match(js,/shape\.finished\.then/);
  assert.match(js,/calendarSelect/); assert.match(js,/openReport/); assert.match(js,/haochenNativeEscape/);
});
