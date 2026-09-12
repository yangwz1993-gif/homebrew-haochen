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
  assert.equal(state.settings.palette,'sage'); assert.equal(state.settings.dock,'notch'); assert.equal(state.settings.motion,'system');
  assert.equal(state.updatedAt,'');
});

test('notch is default while explicit pet-only and side modes are preserved',() => {
  for (const dock of ['notch','pet','side']) assert.equal(ui.normalizeState({settings:{dock}}).settings.dock,dock);
  assert.equal(ui.normalizeState({settings:{dock:'unknown'}}).settings.dock,'notch');
});

test('connection overview keeps unavailable providers visible without inventing events',() => {
  const connectors=[{id:'otty',status:'unknown'},{id:'browser',status:'disabled'},{id:'wechat',status:'limited'},{id:'calendar',status:'permission_required'},{id:'feishu',status:'unsupported'}];
  assert.deepEqual(ui.visibleConnectors(connectors).map(item=>item.id),['otty','browser','wechat','calendar']);
  assert.equal(ui.connectorLabel({id:'browser',enabled:true,setup:{stage:'extension'}}),'待确认扩展连接');
  assert.notEqual(ui.connectorLabel({id:'browser',enabled:true,setup:{stage:'ready'},status:'error'}),'已连接');
  assert.equal(ui.connectorLabel({id:'browser',enabled:false,status:'connected',setup:{stage:'ready'}}),'未开启');
});

test('Chrome checklist only completes steps with explicit observed evidence',() => {
  assert.ok(ui.browserSteps({id:'browser',enabled:true,status:'connected'}).every(step=>!step.done));
  const steps=ui.browserSteps({setup:{stage:'authorization',stepResults:[{id:'extension',status:'complete'},{id:'bridge',status:'complete'},{id:'authorization',status:'attention'},{id:'ready',status:'pending'}]}});
  assert.deepEqual(steps.map(step=>step.done),[true,true,false,false]);
  assert.equal(steps[2].current,true); assert.equal(steps[2].status,'attention');
  assert.ok(ui.browserSteps({setup:{stage:'ready'}}).every(step=>!step.done));
});

test('Otty unknown run state explains missing telemetry rather than lost content',() => {
  const warning=ui.eventCoverage({source:'otty',status:'unknown',incomplete:true},{});
  assert.match(warning.copy,/运行状态/); assert.match(warning.copy,/不代表会话内容丢失/); assert.equal(warning.action,'otty-check');
  assert.equal(ui.eventCoverage({source:'otty',status:'unknown',diagnostics:{message:'当前会话未加载状态集成'}}).copy,'当前会话未加载状态集成');
  assert.doesNotMatch(ui.eventCoverage({source:'otty',status:'unknown'},{diagnostics:{message:'别的 Agent 没上报'}}).copy,/别的 Agent/);
  assert.equal(ui.eventCoverage({source:'Chrome',status:'changed',incomplete:false},{}),null);
});

test('reporting Otty panes never inherit an incomplete collector or another pane warning',() => {
  for (const status of ['processing','idle','awaiting']) {
    const event={id:'otty:current',source:'otty',status,incomplete:true,reasonCode:null,stale:false};
    assert.equal(ui.eventCoverage(event,{status:'partial',diagnostics:{message:'另外两个 Agent 未上报'}}),null);
    assert.equal(ui.eventDetailState(event).coverage,null);
  }
  const unknown=ui.eventCoverage({source:'otty',status:'unknown',reasonCode:'lifecycle_not_reported'});
  assert.equal(unknown.action,'otty-check');
  const stale=ui.eventCoverage({source:'otty',status:'idle',stale:true,incomplete:true});
  assert.equal(stale.action,'connection-refresh'); assert.match(stale.copy,/已过期/);
  assert.equal(ui.eventStateLabel({source:'otty',status:'idle',stale:true}),'状态已过期（上次：就绪）');
  assert.equal(ui.eventStateLabel({source:'otty',status:'unknown',stale:true}),'状态已过期');
  assert.equal(ui.eventStateLabel({source:'otty',status:'processing',stale:false}),'处理中');
});

test('detail semantic signature ignores poll timestamps and other panes, not real changes',() => {
  const event={id:'otty:current',source:'otty',status:'processing',title:'当前 Agent',summary:'正在处理',incomplete:true,
    updatedAt:'2026-09-13T01:00:00Z',observedAt:'2026-09-13T01:00:00Z',unread:true,attentionVersion:'A',
    target:{kind:'otty',paneId:'p:1'},evidence:[{label:'状态',text:'真实状态',capturedAt:'2026-09-13T01:00:00Z'}]};
  const view={kind:'event',id:event.id}, state={events:[event],connectors:[{id:'otty',status:'partial',checkedAt:'old'}]};
  const signature=ui.modalStateSignature(state,view), poll=structuredClone(state);
  poll.events[0].updatedAt='2026-09-13T02:00:00Z'; poll.events[0].observedAt='new';
  poll.events[0].evidence[0].capturedAt='new'; poll.events[0].unread=false; poll.events[0].attentionVersion='B';
  poll.events[0].incomplete=false; poll.events.push({id:'otty:other',status:'unknown'});
  poll.connectors[0].checkedAt='new'; poll.connectors[0].diagnostics={message:'其他 Agent 未上报'};
  assert.equal(ui.modalStateSignature(poll,view),signature);
  for (const patch of [{status:'idle'},{summary:'处理结束'},{error:'当前来源失联'},{stale:true},{status:'permission_required'},{authorized:false},{target:{kind:'otty',paneId:'p:2'}}]) {
    assert.notEqual(ui.modalStateSignature({...state,events:[{...event,...patch}]},view),signature);
  }
  assert.notEqual(ui.modalStateSignature({...state,events:[{...event,evidence:[{label:'状态',text:'新的真实内容'}]}]},view),signature);
  assert.notEqual(ui.modalStateSignature({...state,events:[]},view),signature);
});

test('WeChat connection labels describe Dock badge scope rather than full messages',() => {
  assert.equal(ui.connectorLabel({id:'wechat',enabled:false,status:'connected'}),'未开启');
  assert.equal(ui.connectorLabel({id:'wechat',enabled:true,status:'connected'}),'未读标记可读');
  assert.equal(ui.connectorLabel({id:'wechat',enabled:true,status:'ready'}),'未读标记可读');
  for (const status of ['limited','partial']) assert.equal(ui.connectorLabel({id:'wechat',enabled:true,status}),'标记暂不可读');
  assert.equal(ui.connectorLabel({id:'wechat',enabled:true,status:'permission_required'}),'需辅助功能授权');
  assert.equal(ui.connectorLabel({id:'wechat',enabled:true,status:'not_running'}),'应用未运行');
});

test('WeChat Dock-only events never imply full chat access or missing badge means zero',() => {
  const coverage=ui.eventCoverage({source:'wechat',reasonCode:'dock_badge_only',status:'changed'},{});
  assert.match(coverage.copy,/Dock/); assert.match(coverage.copy,/不是聊天正文/);
  assert.match(coverage.copy,/不会当作 0 条/); assert.match(coverage.copy,/不会定位某个聊天/);
});

test('WeChat has an app jump and only missing AX grants get a manual settings entry',() => {
  assert.deepEqual(ui.wechatControls({status:'permission_required'}).map(item=>item.action),['wechat-open','wechat-permission']);
  for (const status of ['disabled','connected','limited','not_running','error']) assert.deepEqual(ui.wechatControls({status}).map(item=>item.action),['wechat-open']);
  assert.match(asset('dashboard.js'),/perform\('openSource',\{connectorId:'wechat'\},el\)/);
  assert.match(asset('dashboard.js'),/perform\('openSource',\{permission:'accessibility'\},el\)/);
});

test('read receipts require server unread state and the exact displayed revision',() => {
  assert.deepEqual(ui.eventReceipt({id:'e1',attentionVersion:'revision-A',unread:true}),{eventId:'e1',version:'revision-A'});
  assert.deepEqual(ui.eventReceipt({id:'e1',attentionVersion:'revision-B',unread:true}),{eventId:'e1',version:'revision-B'});
  for(const event of [{id:'e1',attentionVersion:'A',unread:false},{id:'e1',unread:true},{attentionVersion:'A',unread:true},{id:'e1',attentionVersion:'A',unread:'true'}]) assert.equal(ui.eventReceipt(event),null);
  assert.equal(ui.ACTIONS.has('eventRead'),true); assert.equal(ui.ACTIONS.has('ottyCheck'),true);
});

test('calendar details acknowledge the exact displayed calendar event, not a different event projection',() => {
  const calendar={id:'calendar:original-id',attentionVersion:'calendar-revision-A',unread:true};
  const otherProjection={...calendar,attentionVersion:'older-event-revision'};
  const state={calendar:[calendar],events:[otherProjection]};
  assert.equal(ui.detailEvent(state,{kind:'calendar',id:calendar.id}),calendar);
  assert.deepEqual(ui.eventReceipt(ui.detailEvent(state,{kind:'calendar',id:calendar.id})),{eventId:'calendar:original-id',version:'calendar-revision-A'});
  assert.equal(ui.detailEvent(state,{kind:'event',id:calendar.id}),otherProjection);
  assert.equal(ui.detailEvent(state,{kind:'report',id:calendar.id}),null);
  assert.equal(ui.detailEvent(state,{kind:'calendar',id:'missing'}),null);
  assert.equal(ui.detailEvent({events:[calendar]},{kind:'calendar',id:calendar.id}),calendar);
  assert.equal(ui.eventReceipt(ui.detailEvent({calendar:[{...calendar,unread:false}]},{kind:'calendar',id:calendar.id})),null);
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
