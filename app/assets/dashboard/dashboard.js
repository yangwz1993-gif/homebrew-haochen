/* haochen 0.5 local dashboard. External content is rendered as text, never HTML.
 * Outbound: {v:1,id,action,payload} -> window.webkit.messageHandlers.haochen.
 * Inbound: window.haochenReceive({id?,ok?,error?,state?}). State is authoritative;
 * the UI never invents successful connections, tracking conclusions or reports.
 */
(function (global) {
  'use strict';
  const PALETTES = Object.freeze({glass:'玻璃质感', sage:'浅雾绿', stone:'暖白石墨', mist:'冷白雾蓝', carbon:'中性炭灰'});
  const FREQUENCIES = Object.freeze({manual:'仅手动', quarter:'每 15 分钟', hourly:'每小时', daily:'每天'});
  const ACTIONS = new Set(['ready','refresh','openSource','trackCreate','trackUpdate','trackPause','trackRefresh','trackDelete','reportGet','pickFolder','fileRemove','askHaochen','openSettings','collapse','connectorEnable','settingsUpdate','browserInstall','browserExtensionFolder','calendarList','calendarSelect','ottyCheck','ottySetup','eventRead','eventDismiss']);
  const STATUS = Object.freeze({connected:'已连接', disconnected:'连接已断开', not_connected:'尚未连接', disabled:'未开启', available:'内容可用', permission_required:'需要授权', limited:'能力受限', partial:'内容不完整', unavailable:'暂不可用', not_running:'应用未运行', tab_closed:'标签页已关闭', target_changed:'原标签页已切换', suspended:'标签页已休眠', reading:'读取中', error:'检查失败', failed:'检查失败', running:'进行中', processing:'处理中', working:'进行中', busy:'检查中', checking:'检查中', awaiting:'等待确认', waiting:'等待确认', needs_attention:'需要关注', completed:'已完成', complete:'已完成', success:'已更新', changed:'有变化', unchanged:'未发现变化', idle:'就绪', paused:'已暂停', pending:'等待检查', unknown:'状态未知', unavailable_source:'来源不可用', stale:'状态已过期', unsupported:'暂不支持', warning:'需要关注', ready:'就绪'});
  const list = (value, limit = 1000) => Array.isArray(value) ? value.filter(item => item && typeof item === 'object' && !Array.isArray(item)).slice(0, limit) : [];
  // Back-end source snapshots can contain 48,000 characters. Preserve their
  // complete, already-bounded evidence rather than silently clipping at an AI
  // summary's much smaller limit.
  const text = (value, limit = 60000) => typeof value === 'string' ? value.slice(0, limit) : typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
  const record = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  function safeURL(value) {
    try {
      const url = new URL(text(value, 4096));
      return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  function normalizeState(value) {
    const raw = record(value), settings = record(raw.settings);
    return {events:list(raw.events), tracks:list(raw.tracks,200), files:list(raw.files,64), connectors:list(raw.connectors,50), reports:list(raw.reports,370), calendar:list(raw.calendar,100), settings:{...settings, palette:Object.hasOwn(PALETTES,settings.palette) ? settings.palette : 'sage', dock:['notch','pet','side'].includes(settings.dock) ? settings.dock : 'notch', motion:settings.motion === 'reduced' ? 'reduced' : 'system'}, updatedAt:text(raw.updatedAt), error:text(raw.error), incomplete:raw.incomplete === true};
  }
  function localDate(date = new Date()) {
    return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  }
  function validDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(`${value}T12:00:00`);
    return Number.isFinite(date.getTime()) && localDate(date) === value;
  }
  function statusLabel(value) { const key=text(value,60); return Object.hasOwn(STATUS,key) ? STATUS[key] : '状态未知'; }
  function eventStateLabel(event) {
    const item=record(event), state=item.status || item.state, source=text(item.source,60).toLowerCase(), target=record(item.target);
    let label=statusLabel(state), contextual={};
    if (['browser','chrome'].includes(source) || target.kind==='browser') {
      contextual={available:'已读取页面文字',permission_required:'需要网站授权',reading:'正在读取页面',disconnected:'Chrome 已断开',error:'页面读取失败'};
    } else if (source==='wechat' || target.kind==='wechat' || item.reasonCode==='dock_badge_only') {
      contextual={available:'已获取未读标记',permission_required:'需辅助功能授权'};
    }
    if (Object.hasOwn(contextual,state)) label=contextual[state];
    // An old successful read must stay visibly old; available describes the
    // collected source data, never completion of an Agent task or read chats.
    if (item.stale===true || state==='stale') return state && !['unknown','stale'].includes(state) ? `状态已过期（上次：${label}）` : '状态已过期';
    return label;
  }
  function eventReceipt(event) {
    const item = record(event), id = text(item.id,200), version = text(item.attentionVersion,200);
    return item.unread === true && id && version ? {eventId:id,version} : null;
  }
  function detailEvent(state, view) {
    const source = record(state), current = record(view);
    if (!['event','calendar'].includes(current.kind)) return null;
    const events = current.kind==='calendar' ? [...list(source.calendar),...list(source.events)] : list(source.events);
    return events.find(item=>text(item.id)===current.id) || null;
  }
  function visibleConnectors(connectors) { return list(connectors,50).filter(item => !/^(lark|feishu)$/.test(text(item.id))); }
  function feedEvents(events) {
    // 动态列表口径：日历走独立区；已处理（已读）的 hi 消息剔除；Hi 空占位 hi:none 保留。
    return list(events,200).filter(item => item && item.type !== 'calendar' && item.kind !== 'calendar'
      && !(String(item.id || '').startsWith('hi:msg:') && item.unread === false));
  }
  function feedEmptyState(events, received, connected) {
    // 动态区空态占位：有内容时返回 null（绝不应显示空态卡）；空态按连接进度分文案。
    // 抽出为纯函数：空→非空切换的决策可单测（B-10 防回归）。
    if (list(events,200).length) return null;
    if (!received) return {title:'正在连接你的本机服务', copy:'正在读取本地连接状态，不会载入示例消息。', glyph:'bell', action:null, actionText:null};
    if (connected) return {title:'此刻，没有新的动态。', copy:'已连接来源的真实变化会出现在这里。不打扰，也不遗漏。', glyph:'bell', action:'connections', actionText:'连接应用'};
    return {title:'给重要的消息，留一个位置。', copy:'连接 Agent、浏览器或日历，让真实变化自然浮现。', glyph:'bell', action:'connections', actionText:'连接应用'};
  }
  function feedTimeBucket(value) {
    // 小时粒度同桶：动态列表的刷新签名用它，避免分钟级时间跳动触发全列表重建。
    // 注意：时间戳是绝对值（适配器每次轮询会刷新 updatedAt），桶只随「数据时间跨小时」变化。
    const date = typeof value === 'number' ? new Date(value < 1e12 ? value * 1000 : value) : new Date(text(value));
    if (!Number.isFinite(date.getTime())) return '';
    return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}-${date.getHours()}`;
  }
  function feedEventSignature(event) {
    // 影响一张动态卡片渲染结果的全部字段；签名不变 = 卡片可以完全复用（不重建、不重播动画）。
    const item = record(event);
    return [text(item.id,200), text(item.source || item.sourceId,60), text(item.title),
            text(item.summary || item.description), text(item.status || item.state),
            item.stale===true, item.unread===true, text(item.attentionVersion,64),
            feedTimeBucket(item.occurredAt || item.updatedAt),
            Boolean(item.target && typeof item.target==='object')].join('\u0001');
  }
  function computeFeedDiff(known, events) {
    // known: {id: signature}（上次渲染的行签名表）；events: 本次事件列表。
    // → add（新出现，播入场动画）/ update（内容真变，更新节点）/ reuse（原样复用）/ remove（消失）。
    const next = new Map();
    for (const event of list(events,200)) {
      const id = text(event && event.id,200);
      if (id) next.set(id, feedEventSignature(event));
    }
    const add = [], update = [], reuse = [];
    for (const [id, sig] of next) {
      if (!Object.hasOwn(known, id)) add.push(id);
      else if (known[id] !== sig) update.push(id);
      else reuse.push(id);
    }
    const remove = Object.keys(record(known)).filter(id => !next.has(id));
    return {add, update, reuse, remove, signatures: Object.fromEntries(next)};
  }
  function connectorLabel(connector) {
    const item = record(connector), stage = record(item.setup).stage;
    // C-11：二维状态优先——连接态只管管道；内容态的问题以「· N 项待处理」跟在已连接后面，
    // 不再把内容问题降级成「能力受限」这种四不像。
    const conn = item.connection, cov = record(item.coverage);
    if (item.id==='wechat') {
      if (item.enabled===false || item.status==='disabled') return '未开启';
      if (item.status==='connected' || item.status==='ready') return '未读标记可读';
      if (item.status==='limited' || item.status==='partial') return '标记暂不可读';
      if (item.status==='permission_required') return '需辅助功能授权';
    }
    if (/browser|chrome/.test(text(item.id))) {
      if (item.enabled === false) return '未开启';
      if (stage === 'extension') return '待确认扩展连接';
      if (stage === 'bridge') return '待连接本机';
      if (stage === 'site' || stage === 'authorization') return '待授权页面';
      if (stage === 'ready' && (conn ? conn === 'connected' : item.status === 'connected')) {
        return cov.level === 'partial' && cov.broken > 0 ? `已连接 · ${cov.broken} 项待处理` : '已连接';
      }
    }
    if (/otty/.test(text(item.id)) && ['unknown','partial','limited'].includes(item.status)) return '待确认 Agent 状态';
    if (conn) {
      if (conn === 'connected') return cov.level === 'partial' && cov.broken > 0 ? `已连接 · ${cov.broken} 项待处理` : '已连接';
      if (conn === 'disabled') return '未开启';
      if (conn === 'permission_required') return '需要授权';
      if (conn === 'error') return '连接异常';
      if (conn === 'unavailable') return '暂不可用';
    }
    return statusLabel(item.status);
  }
  function browserSteps(connector) {
    const item = record(connector), setup = record(item.setup);
    const stages = ['extension','bridge','authorization','ready'], stage = setup.stage === 'site' ? 'authorization' : setup.stage;
    const current = stages.includes(stage) ? stages.indexOf(stage) : -1;
    return ['确认 Chrome 扩展','连接 haochen 本机服务','授权站点并开始追踪','确认首次读取'].map((title,index) => {
      const result = list(setup.stepResults,4).find(step=>step.id===stages[index]);
      // Do not infer an installed extension or a granted site permission from
      // merely enabling the connector, nor from completing a later step.
      return {title,stage:stages[index],done:result?.status==='complete',current:index===current && result?.status!=='complete',status:result?.status || 'unknown'};
    });
  }
  function eventCoverage(event) {
    const item = record(event), status = item.status || item.state;
    if (item.reasonCode==='dock_badge_only') return {copy:'来源是微信 Dock 图标上的未读标记，不是聊天正文。标记不可读时不会当作 0 条；打开来源只会唤起微信，不会定位某个聊天。'};
    if (/otty/i.test(text(item.sourceId) + text(item.source))) {
      if (item.stale===true || status==='stale') return {copy:'这条 Agent 状态已过期，目前无法确认它的运行情况。下面保留的是上次取得的信息。',action:'connection-refresh',label:'重新检查状态'};
      if (status==='unknown' || item.reasonCode==='lifecycle_not_reported') return {copy:text(record(item.diagnostics).message) || '已找到这个 Agent，但还没有收到可用的运行状态；这不代表会话内容丢失。',action:'otty-check',label:'检查状态连接'};
      // The collector may be partial because a DIFFERENT pane has no hook.
      // A reporting event must never inherit that global setup warning.
      return null;
    }
    if (!item.incomplete) return null;
    return {copy:text(item.coverage || item.incompleteReason) || '这次只取得部分来源内容。下面是已经读到的信息，未读部分暂不作判断。'};
  }
  function eventDetailState(event) {
    if (!event) return null;
    const item=record(event), result={coverage:eventCoverage(item)};
    for (const key of ['id','eventId','source','sourceId','title','summary','description','status','state','error','stale','reasonCode','target','startAt','endAt','allDay','location','available','availability','permission','permissionStatus','authorized','revoked']) {
      if (Object.hasOwn(item,key)) result[key]=item[key];
    }
    result.evidence=list(item.evidence,100).map(evidence=>Object.fromEntries(['id','label','source','text','excerpt','content','coverage','error','url','previewTruncated','truncated'].filter(key=>Object.hasOwn(evidence,key)).map(key=>[key,evidence[key]])));
    return result;
  }
  const POLL_FIELDS = new Set(['checkedAt','observedAt','updatedAt','occurredAt','capturedAt','lastCheckedAt','generatedAt','lastSuccessAt','lastSeenAt','checking']);
  function semanticValue(value) {
    if (Array.isArray(value)) return value.map(semanticValue);
    if (value && typeof value==='object') return Object.fromEntries(Object.keys(value).filter(key=>!POLL_FIELDS.has(key)).sort().map(key=>[key,semanticValue(value[key])]));
    return value;
  }
  function modalStateSignature(state, view) {
    const source=record(state), current=record(view);
    let selected;
    if (['event','calendar'].includes(current.kind)) selected=eventDetailState(detailEvent(source,current));
    else if (current.kind==='track') selected=list(source.tracks).find(item=>text(item.id)===current.id) || null;
    else if (current.kind==='report') selected=list(source.reports).find(item=>item.date===current.date) || null;
    else if (current.kind==='connections') selected={connectors:visibleConnectors(source.connectors),settings:source.settings};
    else return null;
    return JSON.stringify(semanticValue(selected));
  }
  function wechatControls(connector) {
    const item = record(connector);
    return [{label:'打开微信',action:'wechat-open'},...(item.status==='permission_required'?[{label:'打开辅助功能设置',action:'wechat-permission'}]:[])];
  }
  function floatingGeometry(viewportWidth, viewportHeight, requestedWidth, contentHeight) {
    const width = Math.max(80,Math.min(requestedWidth,viewportWidth-36));
    const height = Math.max(80,Math.min(Math.max(180,contentHeight),viewportHeight-34));
    return {left:Math.max(0,(viewportWidth-width)/2),top:Math.max(0,(viewportHeight-height)/2),width,height,radius:23};
  }
  function mayEscape(composing, compositionEnd, now) { return !composing && now-compositionEnd>=100; }
  function sourceKind(source) {
    const name = text(source).toLowerCase();
    if (/otty|agent|codex|terminal/.test(name)) return 'terminal';
    if (/chrome|browser|浏览器|safari/.test(name)) return 'globe';
    if (/calendar|日历/.test(name)) return 'calendar';
    if (/^hi$|hi:|hi 待|hi消息/.test(name)) return 'hi';
    if (/wechat|微信/.test(name)) return 'wechat';
    if (/飞书|feishu|lark/.test(name)) return 'message';
    if (/file|folder|文件/.test(name)) return 'folder';
    return 'bell';
  }
  function appLabel(source) {
    const key = text(source,60);
    const connector = (ui.state?.connectors || []).find(item => item.id === key);
    if (connector && connector.name) return connector.name;
    return {otty:'Otty', browser:'Chrome', wechat:'微信', calendar:'日历', hi:'Hi'}[key] || key || '其他';
  }
  function validateTrackDraft(value) {
    const raw = record(value);
    const title = text(raw.title,121).trim(), goal = text(raw.goal,3001).trim();
    if (!title || title.length > 120) return {error:'请填写 1–120 字的事项名称。'};
    if (!goal || goal.length > 3000) return {error:'请填写关注目标（最多 3,000 字）。'};
    if (!Object.hasOwn(FREQUENCIES,raw.frequency)) return {error:'请选择有效的检查频率。'};
    if (!Array.isArray(raw.sources) || raw.sources.length > 12) return {error:'每个事项最多添加 12 个追踪渠道。'};
    const sources = [];
    for (const item of raw.sources) {
      const source = record(item), locator = text(source.locator,2049).trim(), label = text(source.label,121).trim();
      if (!locator && !label) continue;
      if (!locator || locator.length > 2048 || label.length > 120) return {error:'请填写完整的来源地址，名称不超过 120 字。'};
      if (!['url','file','connector'].includes(source.type)) return {error:'请选择有效的追踪渠道类型。'};
      if (source.type === 'url' && !safeURL(locator)) return {error:'网页渠道需要完整的 http 或 https 地址，不能包含账号密码。'};
      if (source.type === 'file' && !locator.startsWith('/')) return {error:'本地渠道请填写完整路径，例如 /Users/你的名字/Documents/反馈.md。'};
      if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(locator)) return {error:'来源地址包含无效字符。'};
      sources.push({...(source.id?{id:text(source.id,80)}:{}),type:source.type, label:label || (source.type === 'url' ? new URL(locator).hostname : locator.split('/').filter(Boolean).at(-1) || locator), locator});
    }
    if (!sources.length) return {error:'至少添加一个追踪渠道，haochen 才知道去哪里检查。'};
    return {value:{...(raw.id ? {id:text(raw.id,200)} : {}),title,goal,frequency:raw.frequency,aiEnabled:raw.aiEnabled === true,sources}};
  }
  class NativeBridge {
    constructor(send, options = {}) {
      this.send = send; this.onState = options.onState || (() => {}); this.onStatus = options.onStatus || (() => {});
      this.timeout = options.timeout || 20000; this.pending = new Map(); this.sequence = 0; this.closed = false;
    }
    request(action, payload = {}) {
      if (!ACTIONS.has(action)) return Promise.reject(new Error('不支持的操作。'));
      if (this.closed) return Promise.reject(new Error('本机服务已关闭。'));
      const id = `hc-${Date.now().toString(36)}-${++this.sequence}`;
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          this.pending.delete(id);
          const error = new Error('本机服务暂未响应。请稍后重试；操作结果以最新状态为准。');
          this.onStatus(false,error.message); reject(error);
        }, this.timeout);
        if (typeof timer.unref === 'function') timer.unref();
        this.pending.set(id,{resolve,reject,timer,action});
        try { this.send({v:1,id,action,payload:record(payload)}); }
        catch (_) { clearTimeout(timer); this.pending.delete(id); const error = new Error('无法连接 haochen 本机服务，请从已安装的 App 打开总览。'); this.onStatus(false,error.message); reject(error); }
      });
    }
    receive(value) {
      const message = record(value);
      if (message.state && typeof message.state === 'object' && !Array.isArray(message.state)) {
        this.onState(message.state); this.onStatus(true,'');
        // WKWebView may deliver the initial JS ready before didFinishNavigation.
        // A real subsequent state push is sufficient proof of that read-only
        // handshake, but never acknowledges any pending mutation implicitly.
        for (const [id,item] of this.pending) if (item.action==='ready') { clearTimeout(item.timer); this.pending.delete(id); item.resolve(message); }
      }
      const pending = typeof message.id === 'string' ? this.pending.get(message.id) : null;
      if (!pending) return;
      this.pending.delete(message.id); clearTimeout(pending.timer);
      if (message.ok === false || message.error) pending.reject(new Error(text(message.error) || '这次操作没有完成，请重试。'));
      else { this.onStatus(true,''); pending.resolve(message); }
    }
    destroy() {
      this.closed = true;
      for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(new Error('本机服务已关闭。')); }
      this.pending.clear();
    }
  }
  const core = {PALETTES,FREQUENCIES,ACTIONS,normalizeState,validateTrackDraft,safeURL,localDate,validDate,statusLabel,eventStateLabel,floatingGeometry,mayEscape,eventReceipt,detailEvent,eventDetailState,modalStateSignature,visibleConnectors,feedEvents,feedEmptyState,feedTimeBucket,feedEventSignature,computeFeedDiff,connectorLabel,browserSteps,eventCoverage,wechatControls,NativeBridge};
  if (typeof module === 'object' && module.exports) module.exports = core;
  global.HaochenDashboardCore = core;
  if (typeof document === 'undefined') return;

  const $ = selector => document.querySelector(selector);
  const app = $('#dashboard'), overview = $('#overview'), layer = $('#float-layer'), win = $('#float-window'), content = $('#float-content'), body = $('#float-body');
  const icon = name => global.HaochenIcons.icon(name);
  const ui = {state:normalizeState({}),connected:false,received:false,error:'',stack:[],phase:'closed',animations:[],animationId:0,drafts:new Map(),busy:new Set(),reportErrors:new Map(),reportLoading:new Set(),readVersions:new Set(),readPending:new Set(),composing:false,compositionEnd:0,toastTimer:null,extensionId:'',resizeFrame:0,calendars:null,calendarSelection:null,calendarLoading:false,calendarError:'',calendarTimer:null};
  const overviewSignatures = new Map();
  let overviewPointer = false, overviewPending = false, overviewFrame = 0;
  let modalPointer = false, modalPending = false, modalFrame = 0;
  function node(tag, className, copy) {
    const el = document.createElement(tag); if (className) el.className = className; if (copy !== undefined) el.textContent = text(copy); return el;
  }
  function button(copy, action, options = {}) {
    const el = node('button',options.className || 'secondary-button'); el.type = 'button'; el.dataset.action = action;
    if (options.icon) el.append(icon(options.icon)); if (copy) el.append(node('span','',copy));
    if (options.id !== undefined) el.dataset.id = text(options.id,200);
    if (options.key) el.dataset.focuskey = options.key;
    if (options.title) { el.title = options.title; el.setAttribute('aria-label',options.title); }
    if (options.disabled) el.disabled = true;
    return el;
  }
  function paragraph(copy, className = 'subtitle') { return node('p',className,copy); }
  function top() { return ui.stack.at(-1); }
  function announce(copy) { $('#announcer').textContent = text(copy); }
  function toast(copy) {
    clearTimeout(ui.toastTimer); const el = $('#toast'); el.textContent = text(copy,500); el.hidden = false;
    ui.toastTimer = setTimeout(() => { el.hidden = true; },5000);
  }
  function dateObject(value) {
    const date = typeof value === 'number' ? new Date(value < 1e12 ? value * 1000 : value) : new Date(text(value));
    return Number.isFinite(date.getTime()) ? date : null;
  }
  function timeLabel(value, full = false) {
    const date = dateObject(value); if (!date) return '尚未检查';
    const today = localDate(date) === localDate();
    return date.toLocaleString('zh-CN', { ...(full || !today ? {month:'numeric',day:'numeric'} : {}),hour:'2-digit',minute:'2-digit',hour12:false });
  }
  function setBusy(el, busy) {
    if (!el) return; if (busy) { el.dataset.wasDisabled = String(el.disabled); el.disabled = true; el.setAttribute('aria-busy','true'); }
    else { el.disabled = el.dataset.wasDisabled === 'true'; el.removeAttribute('aria-busy'); delete el.dataset.wasDisabled; }
  }
  function errorText(error) { return error instanceof Error ? error.message : text(error) || '操作没有完成，请重试。'; }
  async function perform(action, payload = {}, el = null, success = '') {
    const key = `${action}:${text(payload.id || payload.eventId || payload.fileId || payload.date)}`;
    if (ui.busy.has(key)) return null;
    ui.busy.add(key); setBusy(el,true);
    try { const result = await bridge.request(action,payload); if (success) toast(success); return result; }
    catch (error) { toast(errorText(error)); return null; }
    finally { ui.busy.delete(key); setBusy(el,false); }
  }
  const bridge = new NativeBridge(message => {
    const handler = global.webkit && global.webkit.messageHandlers && global.webkit.messageHandlers.haochen;
    if (!handler || typeof handler.postMessage !== 'function') throw new Error('Native bridge unavailable');
    handler.postMessage(message);
  }, {
    onState:raw => {
      const previous = ui.state, next = normalizeState(raw);
      const catalogChanged = JSON.stringify(previous.files.map(item=>[item.id,item.path]))!==JSON.stringify(next.files.map(item=>[item.id,item.path])) || JSON.stringify(previous.events.map(item=>[item.id,item.title]))!==JSON.stringify(next.events.map(item=>[item.id,item.title]));
      ui.state = next; ui.received = true; ui.connected = true; ui.error = ''; renderOverview();
      updateModalFromState(catalogChanged);
    },
    onStatus:(ok,error) => { ui.connected = ok; ui.error = error; renderService(); },
  });
  global.haochenReceive = message => {
    if (typeof message === 'string') { try { message = JSON.parse(message); } catch (_) { return; } }
    message = record(message);
    bridge.receive(message);
    if (!message.id && message.ok === false && message.error) toast(text(message.error));
    if (message.message) toast(text(message.message));
    if (Array.isArray(message.calendars)) {
      clearTimeout(ui.calendarTimer); ui.calendarLoading = false; ui.calendars = list(message.calendars,100); ui.calendarError = message.ok === false ? '未取得日历列表，请确认系统权限后重试。' : '';
      if (!ui.calendarSelection) ui.calendarSelection = new Set(ui.calendars.filter(item=>item.selected).map(item=>text(item.id)));
      if (top()?.kind==='connections') renderModal(false);
    }
    if (validDate(message.openReport)) {
      if (top()?.kind === 'report') { top().date = message.openReport; renderModal(false); loadReport(message.openReport); }
      else openModal('report',null,$('[data-action="report"]'),{date:message.openReport});
    }
  };
  global.haochenDropChanged = active => $('#file-shelf').classList.toggle('is-drop',active === true);
  global.haochenVisibilityChanged = visible => {
    if (!visible) snapshotView();
    document.body.dataset.suspended = visible ? 'false' : 'true';
    // The native shell owns collapse. Its web view stays alive, including the
    // current floating layer, evidence expansion, drafts and scroll position.
    if (top()) { if (visible) renderModal(false,false); else morph(true,false); }
  };

  function notice(copy, kind = '') {
    const el = node('div',`notice ${kind}`.trim()); el.append(icon(kind === 'error' || kind === 'warning' ? 'alert' : 'info'),node('span','notice-copy',copy)); return el;
  }
  function empty(title, copy, glyph, action, actionText) {
    const el = node('div','empty-state'), symbol = node('span','empty-glyph'); symbol.append(icon(glyph));
    el.append(symbol,node('h3','',title),paragraph(copy)); if (action) el.append(button(actionText,action,{className:'secondary-button',icon:'plus',key:`empty-${action}`})); return el;
  }
  function applyAppearance() {
    document.body.dataset.palette = ui.state.settings.palette; document.body.dataset.motion = ui.state.settings.motion;
  }
  function renderService() {
    const banner = $('#connection-banner'); banner.replaceChildren(); banner.hidden = !ui.error;
    if (ui.error) { banner.className = 'notice error'; banner.append(icon('alert'),node('span','notice-copy',ui.error),button('重试','retry-ready',{className:'text-button'})); }
    const connected = ui.state.connectors.filter(item => item.status === 'connected').length;
    const status = $('#service-status'); status.replaceChildren(node('span','presence-dot'),document.createTextNode(ui.connected ? connected ? `${connected} 个来源已连接` : '本机服务就绪 · 待连接来源' : ui.error ? '本机服务未连接' : '本机连接中'));
    const checked = ui.state.connectors.map(item=>item.checkedAt).filter(value=>dateObject(value)).sort((a,b)=>dateObject(b)-dateObject(a))[0];
    $('#updated-label').textContent = ui.received ? checked ? `最近检查 ${timeLabel(checked)}` : ui.state.updatedAt ? `状态更新 ${timeLabel(ui.state.updatedAt)}` : '尚未收到来源更新' : '正在连接本机服务';
  }
  function renderOverview() {
    // A collector may finish between mouse-down and mouse-up. Keep the actual
    // hit target alive through its click, then render the latest authoritative state.
    if (overviewPointer) { overviewPending = true; return; }
    overviewPending = false;
    const active = document.activeElement, focus = active?.dataset?.focuskey;
    const group = active?.closest('#event-list,#track-list,#calendar-list,#file-list');
    const index = group ? [...group.querySelectorAll('[data-focuskey]')].indexOf(active) : -1;
    const scroll = overview.scrollTop;
    applyAppearance(); renderConnectorsOverview(); renderEvents(); renderTracks(); renderCalendar(); renderFiles(); renderService();
    if (!top() && focus && !active.isConnected) {
      const remaining = group ? [...group.querySelectorAll('[data-focuskey]')] : [];
      const fallback = { 'event-list':'refresh', 'track-list':'track-new', 'calendar-list':'connections', 'file-list':'file-add' };
      const target = findFocus(focus,overview) || remaining[Math.min(Math.max(0,index),remaining.length-1)] || findFocus(fallback[group?.id] || 'refresh',overview);
      target?.focus({preventScroll:true});
    }
    overview.scrollTop = scroll;
  }
  function overviewChanged(key, value) {
    const signature = JSON.stringify(value); if (overviewSignatures.get(key) === signature) return false;
    overviewSignatures.set(key,signature); return true;
  }
  function releaseOverviewPointer() {
    overviewPointer = false; cancelAnimationFrame(overviewFrame);
    overviewFrame = requestAnimationFrame(() => { if (overviewPending) renderOverview(); });
  }
  function renderEvents() {
    // 口径与 core.feedEvents 一致（可单测）：Hi 空占位 hi:none 保留。
    const events = feedEvents(ui.state.events);
    const connected = ui.state.connectors.some(item => item.status === 'connected');
    // 签名按小时粒度取时间桶（feedEventSignature）：分钟级时间跳动不再触发重建。
    // 被守卫跳过时也要把各卡片时间文本原地刷新（文本更新不重播动画）。
    if (!overviewChanged('events',[events.length,events.length ? null : [ui.received,connected],events.slice(0,80).map(feedEventSignature)])) { refreshFeedTimes(events); return; }
    const target = $('#event-list');
    const unread = events.filter(item=>item.unread===true).length;
    $('#event-count').textContent = unread ? `${unread} 条未读` : events.length ? `${events.length} 条` : '';
    const emptyState = feedEmptyState(events, ui.received, connected);
    if (emptyState) {
      // 空态整体替换：注册表一并清空，避免残留节点被误复用。
      ui.feedRows = new Map(); ui.feedModules = new Map();
      if (ui.feedFooter) { ui.feedFooter.remove(); ui.feedFooter = null; }
      target.replaceChildren();
      ui.feedEmpty = empty(emptyState.title, emptyState.copy, emptyState.glyph, emptyState.action, emptyState.actionText);
      target.append(ui.feedEmpty); return;
    }
    // 空→非空切换：启动占位卡不在增量注册表里，必须显式撤掉，否则它会烂在原地（B-10）。
    if (ui.feedEmpty) { ui.feedEmpty.remove(); ui.feedEmpty = null; }
    // A top "需要处理" module (attention/error across all apps) + one module per
    // app, flowed into a 2-column masonry so the whole feed fits one screen.
    const shown = events.slice(0,80);
    const priorityOf = (event) => {
      const st = text(event.status || event.state);
      return event.stale ? '' : ['error','failed','permission_required'].includes(st) ? 'err'
        : ['waiting','awaiting','needs_attention','warning','awaiting_input'].includes(st) ? 'attn'
        : (event.unread===true && ['available','idle','completed','done','upcoming','ongoing','scheduled'].includes(st)) ? 'new'
        : ['running','processing','working','checking','reading','busy'].includes(st) ? 'run' : '';
    };
    const buildRow = (event) => {
      const id = text(event.id,200), kind = sourceKind(event.source || event.sourceId);
      const row = node('div','event-row');
      // B-14：占位卡（reasonCode=empty，如 Hi 空占位）是背景提示不是事项——
      // 用弱化的 div 呈现，不可点、无跳转、无状态点/时间，避免被误认为可操作事项。
      if (event.reasonCode === 'empty') {
        row.classList.add('is-placeholder');
        const elp = node('div','event-card is-placeholder');
        const glyphP = node('span',`source-glyph${kind === 'terminal' ? ' agent' : ''}`); glyphP.append(icon(kind));
        const copyP = node('span','event-copy'); copyP.append(node('span','event-title',event.title || ''),node('span','event-summary',text(event.summary || event.description)));
        elp.append(glyphP,copyP); row.append(elp);
        return row;
      }
      const el = button('','event',{className:'event-card',id,key:`event-${id}`});
      el.classList.toggle('is-unread',event.unread===true);
      const pr = priorityOf(event); if (pr) el.dataset.priority = pr;
      if (event.unread===true) el.setAttribute('aria-label',`未读 · ${text(event.title) || '应用动态'}`);
      const glyph = node('span',`source-glyph${kind === 'terminal' ? ' agent' : ''}`); glyph.append(icon(kind));
      const copy = node('span','event-copy'); copy.append(node('span','event-title',event.title || '未命名动态'),node('span','event-summary',text(event.summary || event.description)));
      const meta = node('span','event-meta'), label = node('span');
      const dot = event.stale ? '' : ['error','failed'].includes(event.status) ? 'error' : ['running','processing','working','checking'].includes(event.status) ? 'running' : ['waiting','awaiting','needs_attention','warning'].includes(event.status) ? 'warning' : '';
      label.append(node('span',`status-dot ${dot}`),document.createTextNode(eventStateLabel(event)));
      meta.append(label,node('span','event-time',timeLabel(event.occurredAt || event.updatedAt))); el.append(glyph,copy,meta);
      row.append(el);
      if (event.target && typeof event.target === 'object') {
        const jump = button('','event-source',{className:'event-jump',id,key:`jump-${id}`});
        jump.append(icon('arrow-up-right')); jump.title = '跳转到来源'; jump.setAttribute('aria-label',`跳转到来源：${text(event.title) || '动态'}`);
        row.append(jump);
      }
      // B-11：iPhone 式忽略按钮（悬停露出，紧邻跳转箭头）；实时状态源（otty）不可忽略。
      if (text(event.source || '') !== 'otty') {
        const dismiss = button('','event-dismiss',{className:'event-dismiss',id,key:`dismiss-${id}`});
        dismiss.append(icon('x')); dismiss.title = '忽略这条动态'; dismiss.setAttribute('aria-label',`忽略这条动态：${text(event.title) || '动态'}`);
        row.append(dismiss);
      }
      return row;
    };
    // 增量更新：模块壳与卡片行按 key 复用，只有新出现的卡片播入场动画。
    // No separate "priority" column: keep every event inside its own app module
    // and let colour (attention/error left-bar) mark what needs handling. Within
    // a module, attention/error float to the top.
    if (!ui.feedModules) { ui.feedModules = new Map(); ui.feedRows = new Map(); }
    const groups = [], byKey = new Map();
    for (const event of shown) {
      const key = text(event.source || event.sourceId || 'other',60);
      let g = byKey.get(key);
      if (!g) { g = {key, source:event.source || event.sourceId, items:[]}; byKey.set(key,g); groups.push(g); }
      g.items.push(event);
    }
    // Fixed module order so modules never swap places as events refresh; only a
    // brand-new app appends after the known ones (then alphabetical, stable).
    const ORDER = ['otty','hi','browser','wechat','calendar'];
    const rank = (s) => { const i = ORDER.indexOf(text(s)); return i < 0 ? ORDER.length : i; };
    groups.sort((a,b) => (rank(a.source) - rank(b.source)) || text(a.source).localeCompare(text(b.source)));
    const attnRank = (e) => ['err','attn'].includes(priorityOf(e)) ? 0 : 1;
    // 渲染差分与被单测钉死的 core.computeFeedDiff 是同一套逻辑（测的就是跑的）。
    const known = {};
    for (const [id, entry] of ui.feedRows) known[id] = entry.sig;
    const diff = computeFeedDiff(known, shown);
    const addSet = new Set(diff.add), updateSet = new Set(diff.update);
    const usedModules = new Set();
    for (const g of groups) {
      usedModules.add(g.key);
      let mod = ui.feedModules.get(g.key);
      if (!mod) {
        const section = node('section','feed-module');
        const head = node('div','module-head');
        const badge = node('span',`group-glyph${sourceKind(g.source) === 'terminal' ? ' agent' : ''}`); badge.append(icon(sourceKind(g.source)));
        const countEl = node('span','module-count','');
        head.append(badge,node('span','module-name',appLabel(g.source)),countEl);
        const rows = node('div','module-rows');
        section.append(head,rows);
        mod = {mod:section, rows, countEl};
        ui.feedModules.set(g.key, mod);
      }
      target.append(mod.mod);  // 按固定顺序归位：已有节点是移动不是重建，不触发动画
      const countText = String(g.items.length);
      if (mod.countEl.textContent !== countText) mod.countEl.textContent = countText;
      const ordered = g.items.map((item, i) => [item, i]);
      ordered.sort((a,b) => (attnRank(a[0]) - attnRank(b[0])) || (a[1] - b[1]));
      for (const [event] of ordered) {
        const id = text(event.id,200), sig = diff.signatures[id];
        const existing = ui.feedRows.get(id);
        if (existing && !addSet.has(id) && !updateSet.has(id)) {
          // 未变化：节点原样复用（引用不变、不重播动画），只原地刷新时间文本。
          existing.row.classList.remove('is-new');
          updateRowTime(existing, event);
          mod.rows.append(existing.row);
        } else {
          const row = buildRow(event);
          if (existing) {
            // B-12：刷新撞悬停——内容真变换节点时把悬停状态带到新节点，
            // 否则新节点丢失 hover，跳转/忽略按钮瞬间收起又展开（间歇闪烁）。
            const keepHover = existing.row.matches(':hover');
            existing.row.remove();  // 内容真变：换新节点，但不加 is-new（不重播动画）
            if (keepHover) {
              row.classList.add('is-hover');
              row.addEventListener('mouseleave', () => row.classList.remove('is-hover'), {once:true});
            }
          } else {
            // 只有新出现的卡片播入场动画；播完即摘除，避免后续移动重放。
            row.classList.add('is-new');
            row.addEventListener('animationend', () => row.classList.remove('is-new'), {once:true});
          }
          mod.rows.append(row);
          ui.feedRows.set(id, {row, sig, timeEl: row.querySelector('.event-time')});
        }
      }
    }
    // 消失的模块与卡片移除（diff.remove 与模块归集同一口径）
    for (const [key, mod] of ui.feedModules) if (!usedModules.has(key)) { mod.mod.remove(); ui.feedModules.delete(key); }
    for (const id of diff.remove) {
      const entry = ui.feedRows.get(id);
      if (entry) { entry.row.remove(); ui.feedRows.delete(id); }
    }
    // 尾部「最近 30 条」提示：常驻节点原地更新，同样不重建。
    if (events.length > 30) {
      if (!ui.feedFooter) ui.feedFooter = paragraph('','compact-empty');
      ui.feedFooter.textContent = `先展示最近 30 条，共 ${events.length} 条；日报中可回顾今日变化。`;
      target.append(ui.feedFooter);
    } else if (ui.feedFooter) { ui.feedFooter.remove(); ui.feedFooter = null; }
  }
  function updateRowTime(entry, event) {
    if (!entry.timeEl || !entry.timeEl.isConnected) return;
    const label = timeLabel(event.occurredAt || event.updatedAt);
    if (entry.timeEl.textContent !== label) entry.timeEl.textContent = label;
  }
  function refreshFeedTimes(events) {
    // 守卫跳过的零变化刷新：只原地更新时间文本（不重建、不重播动画）。
    if (!ui.feedRows || !ui.feedRows.size) return;
    for (const event of list(events,200)) {
      const entry = ui.feedRows.get(text(event && event.id,200));
      if (entry) updateRowTime(entry, event);
    }
  }
  function renderConnectorsOverview() {
    const connectors = visibleConnectors(ui.state.connectors);
    if (!overviewChanged('connector-overview',connectors.map(item=>[item.id,item.name,item.status,item.enabled,connectorLabel(item)]))) return;
    const target = $('#connector-overview'); target.replaceChildren(); target.hidden = !connectors.length;
    for (const connector of connectors) {
      const id = text(connector.id,200), connected = (connector.connection || connector.status) === 'connected';
      const el = button('','auth-dot',{className:'auth-dot',id,key:`auth-${id}`});
      el.dataset.status = connected ? 'connected' : 'pending';
      el.title = `${connector.name || id} · ${connectorLabel(connector)}` + (connected ? '' : '（点击一键连接/授权）');
      el.setAttribute('aria-label', el.title);
      el.append(icon(sourceKind(id))); target.append(el);
    }
  }
  function trackState(track) { return track.completed ? '已完成' : track.paused ? '已暂停' : statusLabel(track.status); }
  function renderTracks() {
    if (!overviewChanged('tracks',ui.state.tracks.map(item=>[item.id,item.title,item.conclusion || item.error || item.goal,trackState(item),timeLabel(item.lastCheckedAt)]))) return;
    const target = $('#track-list'); target.replaceChildren();
    if (!ui.state.tracks.length) { const el = node('div','compact-empty'); el.append(paragraph('你来定义关心的事，haochen 沿着指定渠道持续检查。',''),button('添加第一个事项','track-new',{className:'text-button',icon:'arrow-right',key:'empty-track-new'})); target.append(el); return; }
    for (const track of ui.state.tracks) {
      const id = text(track.id,200), el = button('','track',{className:'track-card',id,key:`track-${id}`});
      el.append(node('span','track-title',track.title || '未命名事项'),node('span','track-conclusion',track.conclusion || track.error || track.goal || '尚未获得检查结果'));
      const meta = node('span','track-meta'); meta.append(node('span','',trackState(track)),node('span','',timeLabel(track.lastCheckedAt))); el.append(meta); target.append(el);
    }
  }
  function renderCalendar() {
    const events = ui.state.calendar.length ? ui.state.calendar : ui.state.events.filter(item => item.type === 'calendar' || item.kind === 'calendar');
    const sorted = events.filter(item => { const date = dateObject(item.endAt || item.startAt); return !date || date.getTime() >= Date.now(); }).sort((a,b) => (dateObject(a.startAt)?.getTime() || 0) - (dateObject(b.startAt)?.getTime() || 0));
    const calendar = ui.state.connectors.find(item => /calendar|日历/.test(text(item.id) + text(item.name)));
    if (!overviewChanged('calendar',[sorted.length ? null : calendar?.status,sorted.slice(0,3).map(item=>[item.id,item.title,item.location,item.allDay,timeLabel(item.startAt || item.occurredAt)])])) return;
    const target = $('#calendar-list'); target.replaceChildren();
    if (!sorted.length) {
      const el = node('div','compact-empty'); el.append(paragraph(calendar?.status === 'connected' ? '接下来的日程暂时空着。' : '连接日历后，下一场安排会在这里等你。',''));
      if (calendar?.status !== 'connected') el.append(button('连接日历','connections',{className:'text-button',icon:'arrow-right',key:'empty-calendar-connect'})); target.append(el); return;
    }
    for (const event of sorted.slice(0,3)) {
      const el = button('','calendar',{className:'calendar-item',id:text(event.id,200),key:`calendar-${text(event.id,200)}`});
      const copy = node('span','calendar-copy'); copy.append(node('strong','',event.title || '未命名日程')); if (event.location) copy.append(node('small','',event.location));
      el.append(node('span','calendar-time',event.allDay ? '全天' : timeLabel(event.startAt || event.occurredAt)),copy); target.append(el);
    }
  }
  function renderFiles() {
    if (!overviewChanged('files',ui.state.files.map(item=>[item.id,item.name,item.path,Boolean(item.error)]))) return;
    const target = $('#file-list'); target.replaceChildren();
    for (const file of ui.state.files) {
      const id = text(file.id,200), chip = node('div','file-chip'), open = button('','file-open',{className:'file-open',id,key:`file-${id}`});
      const copy = node('span'); copy.append(node('strong','',file.name || '文件夹'),node('small','',file.error ? '暂不可访问' : '本地快捷入口')); open.append(icon('folder'),copy);
      if (file.path) open.title = text(file.path,4096);
      chip.append(open,button('','file-remove',{className:'icon-button file-remove',icon:'x',id,key:`file-remove-${id}`,title:`移除 ${text(file.name) || '文件夹'} 的快捷入口（不删除原文件）`})); target.append(chip);
    }
    target.append(button('添加文件夹','file-add',{className:'file-add',icon:'plus',key:'file-add'}));
  }

  function findFocus(key, scope = document) { return [...scope.querySelectorAll('[data-focuskey]')].find(el => el.dataset.focuskey === key); }
  function snapshotView() {
    const current = top(); if (!current) return;
    current.scroll = body.scrollTop; current.focusKey = document.activeElement?.dataset?.focuskey;
    current.openDetails = [...body.querySelectorAll('details[open]')].map(el => el.dataset.evidence);
    if (current.kind === 'edit') readDraft();
  }
  function openModal(kind, id = null, trigger = null, extra = {}) {
    snapshotView();
    const anchor = trigger?.getBoundingClientRect() || win.getBoundingClientRect();
    const view = {kind,id,date:localDate(),scroll:0,openDetails:[],triggerKey:trigger?.dataset?.focuskey,anchor:{left:anchor.left,top:anchor.top,width:anchor.width || 120,height:anchor.height || 40},...extra};
    if (ui.stack.length > 10) ui.stack.shift(); ui.stack.push(view);
    renderModal(true); if (kind === 'report') loadReport(view.date);
  }
  function reduced() { return ui.state.settings.motion === 'reduced' || global.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function rectangle(rect) { return {left:`${rect.left}px`,top:`${rect.top}px`,width:`${rect.width}px`,height:`${rect.height}px`,borderRadius:`${rect.radius || 23}px`}; }
  function cancelMotion() { for (const animation of ui.animations) animation.cancel(); ui.animations = []; }
  function anchorRect(view) {
    const el = view.triggerKey && findFocus(view.triggerKey,overview), rect = el?.getBoundingClientRect() || view.anchor;
    const width = Math.max(55,Math.min(rect?.width || 120,app.clientWidth - 24)), height = Math.max(30,Math.min(rect?.height || 40,130));
    return {left:Math.max(12,Math.min(rect?.left || 24,app.clientWidth-width-12)),top:Math.max(12,Math.min(rect?.top || 24,app.clientHeight-height-12)),width,height,radius:12};
  }
  function morph(open, animate = true, closeView = null) {
    const current = top() || closeView; if (!current) return;
    const wasVisible = !layer.hidden, previous = wasVisible ? win.getBoundingClientRect() : null;
    const opacity = wasVisible ? Number(getComputedStyle(content).opacity) : 0;
    const scrimOpacity = wasVisible ? Number(getComputedStyle($('#float-scrim')).opacity) : 0;
    cancelMotion(); const animationId = ++ui.animationId;
    layer.hidden = false; overview.inert = true; overview.setAttribute('aria-hidden','true');
    const width = floatingGeometry(app.clientWidth,app.clientHeight,current.kind === 'report' ? 700 : 640,180).width;
    const available = Math.max(120,app.clientHeight - 34);
    content.style.width = `${width}px`; content.style.maxHeight = `${available}px`;
    Object.assign(win.style,{width:`${width}px`,height:'auto',maxHeight:`${available}px`});
    const natural = Math.max(180,Math.min(content.scrollHeight,available));
    const target = floatingGeometry(app.clientWidth,app.clientHeight,width,natural);
    const end = open ? target : anchorRect(closeView || current);
    const from = previous && ui.phase !== 'closed' ? {left:previous.left,top:previous.top,width:previous.width,height:previous.height,radius:parseFloat(getComputedStyle(win).borderRadius)||23} : anchorRect(current);
    ui.phase = open ? 'opening' : 'closing';
    Object.assign(win.style,rectangle(end)); content.style.opacity = open ? '1' : '0'; $('#float-scrim').style.opacity = open ? '1' : '0';
    const finish = () => {
      if (animationId !== ui.animationId) return;
      cancelMotion(); Object.assign(win.style,rectangle(end)); ui.phase = open ? 'open' : 'closed';
      if (!open) {
        layer.hidden = true; overview.inert = false; overview.removeAttribute('aria-hidden');
        const focus = closeView?.triggerKey && findFocus(closeView.triggerKey,overview);
        (focus || $('[data-action="report"]')).focus({preventScroll:true});
      }
    };
    if (!animate || reduced() || typeof win.animate !== 'function') { finish(); return; }
    const shape = win.animate([rectangle(from),rectangle(end)],{duration:open?460:340,easing:open?'cubic-bezier(.2,.82,.22,1)':'cubic-bezier(.3,.02,.3,1)',fill:'forwards'});
    const fade = content.animate([{opacity},{opacity:open?1:0}],{duration:open?260:150,easing:'ease-out',fill:'forwards'});
    const scrim = $('#float-scrim').animate([{opacity:scrimOpacity},{opacity:open?1:0}],{duration:open?240:280,fill:'forwards'});
    ui.animations = [shape,fade,scrim]; shape.finished.then(finish).catch(() => {});
  }
  function closeModal() {
    if (!top() || ui.phase === 'closing') return;
    if (top().ottyPlan) { top().ottyPlan=null; renderModal(false); return; }
    if (top().confirm) { top().confirm = false; renderModal(false); return; }
    snapshotView(); const closed = ui.stack.pop();
    if (top()) renderModal(true); else morph(false,true,closed);
  }
  function closeAll(animate = true) {
    if (!top()) return; snapshotView(); const first = ui.stack[0]; ui.stack = []; morph(false,animate,first);
  }
  function updateModalFromState(catalogChanged = false) {
    const current=top();
    if (!current || document.body.dataset.suspended==='true' || current.kind==='edit' && !catalogChanged) return;
    const signature=modalStateSignature(ui.state,current);
    if (signature!==null && current.stateSignature===signature) {
      // Preserve selection, focus, scroll and the exact action elements across
      // ordinary polls. A new unread revision with identical visible content
      // can still be acknowledged without replacing those elements.
      requestAnimationFrame(()=>markVisibleEvent(current)); return;
    }
    if (modalPointer) { modalPending=true; return; }
    renderModal(false);
  }
  function releaseModalPointer() {
    modalPointer=false; cancelAnimationFrame(modalFrame);
    modalFrame=requestAnimationFrame(()=>{ if (modalPending) { modalPending=false; updateModalFromState(true); } });
  }
  function renderModal(animate = false, capture = true) {
    const current = top(); if (!current) return;
    if (!animate && capture) snapshotView();
    const titles = {event:'应用动态',track:'事项详情',edit:current.id?'编辑追踪':'添加事项',report:'日报',connections:'连接与外观',calendar:'日程详情'};
    const caption = $('#float-title'); caption.replaceChildren(icon(current.kind === 'report' ? 'notebook' : current.kind === 'connections' ? 'settings' : current.kind === 'event' ? 'bell' : 'target'),document.createTextNode(`haochen / ${titles[current.kind] || '详情'}`));
    body.replaceChildren();
    if (current.kind === 'event') renderEventDetail(current);
    else if (current.kind === 'track') renderTrackDetail(current);
    else if (current.kind === 'edit') renderTrackEditor(current);
    else if (current.kind === 'report') renderReport(current);
    else if (current.kind === 'connections') renderConnections();
    else if (current.kind === 'calendar') renderCalendarDetail(current);
    current.stateSignature=modalStateSignature(ui.state,current);
    for (const detail of body.querySelectorAll('details')) if (current.openDetails?.includes(detail.dataset.evidence)) detail.open = true;
    morph(true,animate); body.scrollTop = current.scroll || 0;
    requestAnimationFrame(() => {
      if (top() !== current || ui.phase === 'closing' || document.body.dataset.suspended==='true') return;
      body.scrollTop = current.scroll || 0;
      if (current.focusConnector) {
        const card = [...body.querySelectorAll('[data-connector]')].find(el=>el.dataset.connector===current.focusConnector);
        if (card) { body.scrollTop = Math.max(0,card.offsetTop-body.offsetTop-10); current.scroll=body.scrollTop; }
        delete current.focusConnector;
      }
      const focus = current.focusKey && findFocus(current.focusKey,body);
      (focus || $('[data-focuskey="modal-close"]')).focus({preventScroll:true});
      markVisibleEvent(current);
    });
  }
  function markVisibleEvent(view) {
    if (!['event','calendar'].includes(view.kind) || top()!==view || document.body.dataset.suspended==='true') return;
    const receipt = eventReceipt(detailEvent(ui.state,view));
    if (!receipt) return;
    const key = JSON.stringify([receipt.eventId,receipt.version]);
    if (!view.readAttempts) view.readAttempts = new Set();
    if (view.readAttempts.has(key) || ui.readVersions.has(key) || ui.readPending.has(key)) return;
    view.readAttempts.add(key); ui.readPending.add(key);
    bridge.request('eventRead',receipt).then(() => {
      // Only the reply's authoritative state can clear a badge. A late reply
      // for revision A must never mark a newly-arrived revision B as read.
      ui.readVersions.add(key); if (ui.readVersions.size>256) ui.readVersions.delete(ui.readVersions.values().next().value);
    }).catch(() => { if (top()===view) toast('已读状态暂未保存。稍后重新打开这条动态可重试。'); }).finally(()=>ui.readPending.delete(key));
  }
  function section(title) { const el = node('section','detail-section'); el.append(node('h2','',title)); return el; }
  function metadata(values) {
    const el = node('div','meta-row');
    for (const [glyph,copy] of values) if (copy) { const item = node('span'); item.append(icon(glyph),document.createTextNode(copy)); el.append(item); }
    return el;
  }
  function renderEvidence(items, parent) {
    let index = 0;
    for (const item of list(items,100)) {
      const details = node('details','evidence'); details.dataset.evidence = text(item.id || item.label || `evidence-${index++}`,300);
      const summary = node('summary'); summary.append(icon('chevron-right'),document.createTextNode(text(item.label || item.source || '来源依据')));
      const copy = node('div','evidence-content'); copy.append(paragraph(item.text || item.excerpt || item.content || '这条来源未提供可展示的正文。',''));
      if (item.capturedAt || item.checkedAt || item.updatedAt) copy.append(node('small','',`采集于 ${timeLabel(item.capturedAt || item.checkedAt || item.updatedAt,true)}`));
      if (item.coverage) copy.append(node('small','',item.coverage));
      if (item.error) copy.append(paragraph(item.error,'form-error'));
      if (safeURL(item.url)) { const open = button('打开来源','evidence-open',{className:'text-button',icon:'arrow-up-right'}); open.dataset.url = safeURL(item.url); copy.append(open); }
      details.append(summary,copy); parent.append(details);
    }
  }
  function renderEventDetail(view) {
    const event = detailEvent(ui.state,view);
    if (!event) { body.append(empty('这条动态已不可用','来源已移除，或当前无法再取得这条动态。返回总览可以查看仍可用的最新信息。','bell')); return; }
    body.append(node('div','eyebrow',text(event.source) || '应用动态'),node('h1','',event.title || '未命名动态'),metadata([['clock',timeLabel(event.occurredAt || event.updatedAt,true)],['info',eventStateLabel(event)]]));
    const coverage = eventCoverage(event);
    if (coverage) { const info = notice(coverage.copy); if (coverage.action) info.append(button(coverage.label,coverage.action,{className:'text-button',key:'event-status-check'})); body.append(info); }
    if (event.error) body.append(notice(event.error,'error'));
    body.append(paragraph(event.summary || event.description || '来源尚未提供这条动态的摘要。','body-copy'));
    const actions = node('div','detail-actions');
    // Only offer "open source" when there is an actual jump target (e.g. Otty
    // pane, browser tab) or the WeChat dock case. Hi follow-ups have no place to
    // jump to, so we don't show a dead button.
    const canOpen = event.reasonCode==='dock_badge_only' || (event.target && typeof event.target==='object');
    if (canOpen) actions.append(button(event.reasonCode==='dock_badge_only'?'打开微信':'打开来源','event-source',{icon:'arrow-up-right',id:view.id,key:'event-source'}));
    actions.append(button('问 haochen','event-ask',{className:'primary-button',icon:'sparkles',id:view.id,key:'event-ask'})); body.append(actions);
    const evidence = section('内容与依据'); if (list(event.evidence).length) renderEvidence(event.evidence,evidence); else evidence.append(paragraph('这条动态未附带额外正文。haochen 不会将缺失内容当作已读。')); body.append(evidence);
  }
  function renderTrackDetail(view) {
    const track = ui.state.tracks.find(item => text(item.id) === view.id);
    if (!track) { body.append(empty('这条事项已被移除','返回总览可以查看其他追踪事项。','target')); return; }
    body.append(node('div','eyebrow',trackState(track)),node('h1','',track.title || '未命名事项'),paragraph(track.goal));
    const summary = node('div','detail-summary'); summary.append(node('small','',track.aiEnabled ? '最新整理 · AI 已获你的许可' : '最新检查结果 · 未启用 AI 整理'),paragraph(track.conclusion || '尚未获得检查结论。','')); body.append(summary);
    body.append(metadata([['clock',`上次检查 ${timeLabel(track.lastCheckedAt,true)}`],['refresh',FREQUENCIES[track.frequency] || '频率未设置'],['info',track.updatedAt ? `结论更新 ${timeLabel(track.updatedAt,true)}` : ''] ]));
    if (track.incomplete) body.append(notice('部分渠道没有取得完整内容；结论仅依据已取得的来源。','warning'));
    if (track.error) body.append(notice(track.error,'error'));
    const actions = node('div','detail-actions');
    actions.append(button('现在检查','track-refresh',{className:'primary-button',icon:'refresh',id:view.id,key:'track-refresh',disabled:['checking','busy'].includes(track.status)}),button('编辑','track-edit',{icon:'edit',id:view.id,key:'track-edit'}),button(track.paused?'恢复追踪':'暂停追踪','track-pause',{icon:track.paused?'play':'pause',id:view.id,key:'track-pause'}),button('问 haochen','track-ask',{icon:'sparkles',id:view.id,key:'track-ask'})); body.append(actions);
    const sources = section('沿着这些渠道追踪');
    for (const source of list(track.sources,12)) { const line = node('div','source-line'), copy = node('div'); copy.append(node('strong','',source.label || '追踪渠道'),node('small','',source.locator)); line.append(icon(source.type === 'url' ? 'globe' : source.type === 'file' ? 'folder' : 'link'),copy); sources.append(line); }
    if (!list(track.sources).length) sources.append(paragraph('尚未添加渠道，请编辑事项补充。')); body.append(sources);
    const evidence = section('检查依据'); if (list(track.evidence).length) renderEvidence(track.evidence,evidence); else evidence.append(paragraph('还没有可展示的来源快照。')); body.append(evidence);
    const remove = section('事项管理');
    if (view.confirm) { const warning = node('div','inline-confirm'); warning.append(paragraph('移除这条事项并停止后续检查？不会删除原始文件或渠道内容。','')); const controls = node('div','detail-actions'); controls.append(button('确认移除','track-delete',{className:'danger-button',id:view.id,key:'confirm-delete'}),button('保留事项','confirm-cancel',{key:'confirm-cancel'})); warning.append(controls); remove.append(warning); }
    else remove.append(button('移除事项','track-delete-confirm',{className:'text-button danger-text',icon:'trash',id:view.id,key:'track-delete-confirm'})); body.append(remove);
  }
  function getDraft(view) {
    const key = view.id || 'new';
    if (!ui.drafts.has(key)) {
      const track = ui.state.tracks.find(item => text(item.id) === view.id) || {};
      ui.drafts.set(key,{...(view.id?{id:view.id}:{}),title:text(track.title),goal:text(track.goal),frequency:track.frequency || 'hourly',aiEnabled:track.aiEnabled === true,sources:list(track.sources,12).map(source => ({...(source.id?{id:text(source.id,80)}:{}),type:source.type || 'url',label:text(source.label),locator:text(source.locator)}))});
      if (!ui.drafts.get(key).sources.length) ui.drafts.get(key).sources.push({type:'url',label:'',locator:''});
    }
    return ui.drafts.get(key);
  }
  function field(label, child, help = '') {
    const wrap = node('label'); wrap.append(node('span','field-label',label),child); if (help) wrap.append(node('small','field-help',help)); return wrap;
  }
  function input(name, value, placeholder, maxLength) {
    const el = node('input'); el.name = name; el.value = text(value); el.placeholder = placeholder || ''; el.dataset.focuskey = name; if (maxLength) el.maxLength = maxLength; return el;
  }
  function select(name, values, value) {
    const el = node('select'); el.name = name; el.dataset.focuskey = name;
    for (const [key,label] of Object.entries(values)) { const option = node('option','',label); option.value = key; el.append(option); } el.value = value; return el;
  }
  function renderTrackEditor(view) {
    const draft = getDraft(view); body.append(node('h1','',view.id?'编辑追踪事项':'把一件事交给 haochen'),paragraph('你决定关注什么、去哪里检查。进展来自真实来源，不限于某个应用。'));
    const form = node('form','form-grid'); form.id = 'track-form'; form.noValidate = true;
    const title = input('title',draft.title,'例如：关注本周的版本验收',120); title.required = true;
    const goal = node('textarea'); goal.name = 'goal'; goal.dataset.focuskey = 'goal'; goal.value = draft.goal; goal.maxLength = 3000; goal.placeholder = '什么变化值得关注？你希望最终确认什么？'; goal.required = true;
    form.append(field('事项名称',title),field('关注目标',goal));
    const sources = node('section'); sources.append(node('h2','field-label','追踪渠道')); const sourceList = node('div','sources-editor');
    draft.sources.forEach((source,index) => {
      const row = node('div','source-editor'); row.dataset.sourceIndex = String(index); const header = node('div','source-editor-top');
      const type = select(`source-type-${index}`,{url:'网页链接',file:'本地文件 / 文件夹',connector:'已连接来源'},source.type);
      const name = input(`source-label-${index}`,source.label,'来源名称（可选）',120);
      type.setAttribute('aria-label',`第 ${index+1} 个渠道类型`); name.setAttribute('aria-label',`第 ${index+1} 个来源名称（可选）`);
      const remove = button('','source-remove',{className:'icon-button',icon:'x',id:String(index),title:'移除这个渠道'});
      header.append(type,name,remove);
      let locator;
      if (source.type === 'url') { locator = input(`source-locator-${index}`,source.locator,'https://…',2048); locator.autocomplete = 'off'; }
      else {
        const choices = source.type === 'file' ? ui.state.files.map(item=>[text(item.path),text(item.name)]) : ui.state.events.map(item=>[text(item.id),`${text(item.source)} · ${text(item.title)}`]);
        const options = {'':source.type === 'file'?'选择文件条中已授权的入口':'选择一条真实应用动态'};
        for (const [key,label] of choices) if (key) options[key] = label;
        if (source.locator && !Object.hasOwn(options,source.locator)) options[source.locator] = '已保存的来源（当前不可读）';
        locator = select(`source-locator-${index}`,options,source.locator);
      }
      locator.setAttribute('aria-label',`第 ${index+1} 个追踪渠道地址`);
      row.append(header,locator);
      if (source.type === 'file') row.append(button('选择新的本地文件或文件夹','file-add',{className:'text-button',icon:'folder-plus',key:`pick-source-${index}`}));
      if (source.type === 'connector' && !ui.state.events.length) row.append(node('small','field-help','还没有真实动态。先连接来源，或使用网页与本地渠道。'));
      sourceList.append(row);
    });
    sources.append(sourceList,button('再加一个渠道','source-add',{className:'text-button',icon:'plus',key:'source-add',disabled:draft.sources.length>=12}),node('small','field-help','网页需先在 Chrome 扩展中按站点授权并选择追踪；本地文件从你已授权的文件条选择。不会自动搜索全部应用。')); form.append(sources);
    form.append(field('检查频率',select('frequency',FREQUENCIES,draft.frequency),'App 运行时按此频率检查；唤醒或重新启动后会补查到期事项。'));
    const opt = node('label','check-row'), checkbox = input('aiEnabled'); checkbox.type = 'checkbox'; checkbox.checked = draft.aiEnabled; const desc = node('span','', '允许 AI 整理这个事项'); desc.append(node('small','','将已取得的相关来源内容发送到你设置的模型服务，用于分析进展。关闭时仅检查来源变化，不生成 AI 结论。')); opt.append(checkbox,desc); form.append(opt);
    const error = node('p','form-error'); error.id = 'track-form-error'; error.setAttribute('role','alert'); error.tabIndex = -1; form.append(error);
    const actions = node('div','form-actions'); actions.append(node('span','','Esc 关闭会保留本次编辑草稿')); const save = button(view.id?'保存更改':'开始追踪','track-save',{className:'primary-button',icon:'check',key:'track-save'}); save.type = 'submit'; actions.append(save); form.append(actions); body.append(form);
  }
  function readDraft() {
    const view = top(), form = $('#track-form'); if (!view || view.kind !== 'edit' || !form) return;
    const draft = getDraft(view); draft.title = form.elements.title.value; draft.goal = form.elements.goal.value; draft.frequency = form.elements.frequency.value; draft.aiEnabled = form.elements.aiEnabled.checked;
    const previous = draft.sources;
    draft.sources = [...form.querySelectorAll('[data-source-index]')].map(row => { const index = row.dataset.sourceIndex, type = form.elements[`source-type-${index}`].value, locator = form.elements[`source-locator-${index}`].value;
      return {...(previous[index]?.id?{id:previous[index].id}:{}),type,label:form.elements[`source-label-${index}`].value || (type==='connector'?text(ui.state.events.find(item=>text(item.id)===locator)?.title):''),locator}; });
  }
  async function saveDraft(form) {
    readDraft(); const view = top(); if (!view || view.saving) return;
    const showError = copy => { const error = form.querySelector('#track-form-error'); if (form.isConnected && error) { error.textContent = copy; error.focus({preventScroll:true}); error.scrollIntoView({block:'center',behavior:'auto'}); } toast(copy); announce(copy); };
    const validated = validateTrackDraft(getDraft(view)); if (validated.error) { showError(validated.error); return; }
    view.saving = true; const submit = form.querySelector('[type="submit"]'); setBusy(submit,true);
    try {
      await bridge.request(view.id?'trackUpdate':'trackCreate',validated.value);
      toast(view.id?'追踪设置已保存。':'事项已添加，进展以实际检查结果为准。'); if (top() === view) closeModal(); ui.drafts.delete(view.id||'new');
    } catch (error) { showError(errorText(error)); }
    finally { view.saving = false; setBusy(submit,false); }
  }
  async function loadReport(date, force = false) {
    if (!validDate(date) || ui.reportLoading.has(date)) return;
    if (!force && ui.state.reports.some(report => report.date === date)) return;
    ui.reportLoading.add(date); ui.reportErrors.delete(date); if (top()?.kind === 'report' && top().date === date) renderModal(false);
    try { await bridge.request('reportGet',{date}); }
    catch (error) { ui.reportErrors.set(date,errorText(error)); }
    finally { ui.reportLoading.delete(date); if (top()?.kind === 'report' && top().date === date) renderModal(false); }
  }
  function renderReport(view) {
    const header = node('div','report-heading'); header.append(node('h1','',view.date === localDate()?'今日日报':'往日日报'));
    const date = input('report-date',view.date); date.type = 'date'; date.max = localDate(); date.className = 'report-date'; date.setAttribute('aria-label','选择日报日期'); header.append(date); body.append(header);
    const report = ui.state.reports.find(item => item.date === view.date);
    if (ui.reportLoading.has(view.date)) { const loading = notice('正在读取这一天的真实记录…'); const dots = node('span','loading-dots'); dots.append(node('i'),node('i'),node('i')); loading.prepend(dots); body.append(loading); }
    if (ui.reportErrors.has(view.date)) body.append(notice(ui.reportErrors.get(view.date),'error'));
    if (!report) {
      if (!ui.reportLoading.has(view.date)) body.append(empty('这一天，还没有可展示的日报。','只有已经采集到的应用动态与事项依据才会进入日报，不会补写未发生的内容。','notebook'));
    } else {
      body.append(metadata([['clock',report.generatedAt ? `整理于 ${timeLabel(report.generatedAt,true)}` : ''],['shield',report.aiGenerated ? 'AI 整理 · 请结合来源核对' : '来自本机已采集记录']]));
      if (report.incomplete) body.append(notice(report.coverage || '日报包含的来源不完整；缺失的渠道不代表今天没有变化。','warning'));
      if (report.error) body.append(notice(report.error,'error'));
      if (report.summary) body.append(paragraph(report.summary,'body-copy'));
      for (const group of list(report.sections,30)) {
        const section = node('section','report-section'); section.append(node('h2','',group.title || '今日记录'));
        for (const item of list(group.items,200)) { const entry = node('div','report-entry'); entry.append(paragraph(item.text || item.summary,'')); if (item.updatedAt) entry.append(node('small','',timeLabel(item.updatedAt,true))); renderEvidence(item.evidence,entry); if (item.trackId) entry.append(button('查看事项','track',{className:'text-button',icon:'arrow-up-right',id:text(item.trackId,200),key:`report-track-${text(item.trackId,200)}`})); section.append(entry); }
        body.append(section);
      }
      if (!report.summary && !list(report.sections).length) body.append(paragraph('这一天暂无已采集的动态或事项记录。'));
    }
    const actions = node('div','detail-actions'); actions.append(button('重新读取','report-refresh',{icon:'refresh',key:'report-refresh',disabled:ui.reportLoading.has(view.date)})); if (report) actions.append(button('问 haochen','report-ask',{className:'primary-button',icon:'sparkles',key:'report-ask'})); body.append(actions);
  }
  function renderCalendarDetail(view) {
    const event = detailEvent(ui.state,view);
    if (!event) { body.append(paragraph('这条日程已不在当前列表。')); return; }
    body.append(node('div','eyebrow','日历'),node('h1','',event.title || '未命名日程'),metadata([['clock',`${timeLabel(event.startAt || event.occurredAt,true)}${event.endAt?' — '+timeLabel(event.endAt):''}`]]));
    if (event.location) body.append(paragraph(event.location,'body-copy')); if (event.summary || event.description) body.append(paragraph(event.summary || event.description,'body-copy'));
    const actions = node('div','detail-actions'); actions.append(button('打开日程来源','event-source',{icon:'arrow-up-right',id:view.id})); body.append(actions); renderEvidence(event.evidence,body);
  }
  function renderConnections() {
    body.append(node('h1','','让常用应用连在一起'),paragraph('每个连接单独开启。未授权、不可用与能力受限都会如实显示；不会读取未经允许的内容。'));
    if (!ui.state.connectors.length) body.append(notice('还没有取得连接列表，请检查本机服务是否正常运行。','warning'));
    for (const connector of visibleConnectors(ui.state.connectors)) {
      const id = text(connector.id,200), card = node('section','connector-card'), header = node('div','connector-heading');
      card.dataset.connector = id;
      const name = node('h2','connector-name'); name.append(icon(sourceKind(id)),document.createTextNode(text(connector.name) || id)); header.append(name,node('span','connector-status',connectorLabel(connector))); card.append(header);
      card.append(paragraph(connector.summary || connector.description || '此来源尚未提供连接说明。'));
      // C-11 内容态副文案：连接正常但有内容失效时，单独指出（不污染连接态判定）。
      const cov = record(connector.coverage);
      if ((connector.connection || '') === 'connected' && cov.level === 'partial' && cov.broken > 0) {
        card.append(paragraph(`${cov.broken} 个追踪项需要处理（如旧标签页已关闭）；连接本身正常。`,'field-help'));
      }
      if (connector.error) card.append(paragraph(connector.error,'form-error'));
      const actions = node('div','detail-actions');
      // 按钮反映“是否真的连接上”（二维状态里的连接态），不是“是否启用”：没连上就一直显示“开启连接”。
      const isConnected = (connector.connection || connector.status) === 'connected';
      if (connector.enabled || connector.status!=='unsupported') actions.append(button(isConnected?'关闭连接':connector.status === 'permission_required'?'开启并授权':'开启连接','connector-toggle',{className:isConnected?'secondary-button':'primary-button',id,key:`connector-${id}`}));
      if (safeURL(connector.helpUrl)) { const help = button('连接帮助','evidence-open',{icon:'arrow-up-right',className:'text-button'}); help.dataset.url = safeURL(connector.helpUrl); actions.append(help); }
      if (actions.childNodes.length) card.append(actions);
      if (/otty/.test(id)) renderOttyConnection(connector,card);
      if (/wechat/.test(id)) {
        const diagnosis = record(connector.diagnostics);
        if (diagnosis.message) card.append(paragraph(diagnosis.message,'connector-help'));
        card.append(paragraph(connector.coverage || '仅查看 Dock 上的未读标记，不读取聊天正文，也不会定位某个聊天。标记不可读时显示未知，不当成 0 条。','field-help'));
        const wechatActions = node('div','detail-actions');
        for (const control of wechatControls(connector)) wechatActions.append(button(control.label,control.action,{className:'secondary-button',icon:control.action==='wechat-open'?'arrow-up-right':'settings',key:control.action}));
        card.append(wechatActions);
        if (connector.status==='permission_required') card.append(paragraph('只打开系统设置，由你决定是否授权 haochen；不会替你开启权限。','field-help'));
      }
      if (id==='calendar') {
        const picker = node('div','connector-help'); picker.append(button(ui.calendarLoading?'读取日历中…':'选择要展示的日历','calendar-list',{className:'text-button',icon:'calendar',disabled:ui.calendarLoading,key:'calendar-list'}));
        if (ui.calendarError) picker.append(paragraph(ui.calendarError,'form-error'));
        if (ui.calendars) {
          if (!ui.calendars.length) picker.append(paragraph(connector.status==='connected'?'没有取得可选日历，请在系统日历确认账号和日历是否可用。':'尚未取得日历列表。请先开启连接并授予完整日历访问权限；空列表不代表没有日历。','field-help'));
          for (const calendar of ui.calendars) {
            const label = node('label','check-row'), check = input(`calendar-${text(calendar.id)}`); check.type='checkbox'; check.dataset.calendarId=text(calendar.id); check.checked=ui.calendarSelection?.has(text(calendar.id)); const copy = node('span','',calendar.title || '未命名日历'); if (calendar.account) copy.append(node('small','',calendar.account)); label.append(check,copy); picker.append(label);
          }
          if (ui.calendars.length) picker.append(button('保存日历选择','calendar-save',{className:'secondary-button',icon:'check',key:'calendar-save'}));
        }
        card.append(picker);
      }
      if (/chrome|browser/.test(id)) {
        renderBrowserConnection(connector,card);
      }
      if (id==='hi') renderHiConnection(connector,card);
      body.append(card);
    }
    if (ui.state.connectors.some(item=>/^(lark|feishu)$/.test(text(item.id)))) body.append(paragraph('飞书接入已延期，本轮先把以上来源接好。','field-help'));
    const appearance = section('看起来，像你喜欢的样子');
    const palettes = node('div','palette-grid'); for (const [key,label] of Object.entries(PALETTES)) { const el = button('','palette',{className:'palette-button',key:`palette-${key}`}); el.dataset.palette = key; el.setAttribute('aria-pressed',String(ui.state.settings.palette===key)); el.append(node('span','palette-swatch'),node('span','',label)); palettes.append(el); } appearance.append(palettes);
    const dock = node('div','settings-row'), dockLabel = node('label','','总览入口'), dockSelect = select('dock',{notch:'刘海 + 桌面助手',pet:'仅桌面助手',side:'侧边 + 桌面助手'},ui.state.settings.dock); dockLabel.htmlFor='dock-setting'; dockSelect.id='dock-setting'; dock.append(dockLabel,dockSelect);
    const motion = node('div','settings-row'), motionLabel = node('label','','界面动效'), motionSelect = select('motion',{system:'跟随系统',reduced:'减少动态效果'},ui.state.settings.motion); motionLabel.htmlFor='motion-setting'; motionSelect.id='motion-setting'; motion.append(motionLabel,motionSelect); appearance.append(dock,paragraph('总览在前台时按 ⌘M 收起，重新唤起会回到刚才的位置。无刘海屏幕使用顶部胶囊；桌面助手始终可打开同一个总览。','field-help'),motion); body.append(appearance);
    const model = section('对话与模型'); model.append(paragraph('继续使用你已配置的模型和本机安全存储。这里不会显示 API Key。'),button('打开 haochen 设置','settings',{className:'secondary-button',icon:'settings',key:'model-settings'})); body.append(model);
  }

  function renderHiConnection(connector,card) {
    // Hi authorization happens in the terminal via the hi CLI's OAuth login,
    // not inside this app. Tell the user exactly where, tailored to the state.
    const help = node('div','connector-help'), code = text(connector.reasonCode);
    const steps = node('ol','connection-steps');
    const step = (title, done, current, copy) => {
      const row = node('li',`connection-step${done?' is-complete':''}${current?' is-current':''}`);
      const mark = node('span','step-mark',done?'':String(steps.childNodes.length+1)); if (done) mark.append(icon('check'));
      const body = node('div','step-copy'); body.append(node('strong','',title),node('span','step-status',done?'已就绪':current?'下一步':'待完成'));
      body.append(paragraph(copy,'')); row.append(mark,body); return row;
    };
    const hasCli = code !== 'cli_missing';
    const authed = code !== 'cli_missing' && code !== 'not_authenticated';
    steps.append(step('安装本机 hi 命令行', hasCli, !hasCli,
      '在终端执行：npm install -g @xhs/hi-cli --registry=http://npm.devops.xiaohongshu.com:7001（内网源）。'));
    steps.append(step('在终端登录 hi', authed, hasCli && !authed,
      '在终端运行 hi search:me，按提示完成内部账号 OAuth 登录。授权发生在终端，不在本应用内；haochen 只调用本机命令，不读取或上传你的登录令牌。'));
    steps.append(step('回到这里开启连接', authed && connector.enabled, authed && !connector.enabled,
      '登录后点上方“开启连接”。haochen 只读聚合你本人的今日待跟进日程与待处理任务，不发消息、不读聊天正文，任务计数不是聊天未读数。'));
    help.append(steps);
    card.append(help);
  }

  function renderOttyConnection(connector,card) {
    const help = node('div','connector-help'), diagnosis = record(connector.diagnostics), agents = list(diagnosis.agents,20);
    if (diagnosis.message) help.append(paragraph(diagnosis.message,''));
    help.append(button('检查状态连接','otty-check',{className:'text-button',icon:'refresh',key:'otty-check'}));
    if (!agents.length) help.append(paragraph('先检查当前 Agent 和官方集成状态，再决定是否需要配置。已找到会话不等于已经取得运行状态。',''));
    for (const agent of agents) {
      const kind = text(agent.kind,80), row = node('div','agent-diagnostic');
      row.append(node('strong','',agent.label || kind),paragraph(agent.message || '还没有取得这个 Agent 的状态说明。',''));
      if (agent.canInstall===true && agent.nextAction==='confirm_install') {
        const install = button('配置官方状态集成','otty-setup-plan',{className:'secondary-button',icon:'link',id:kind,key:`otty-plan-${kind}`}); row.append(install);
        if (top()?.ottyPlan?.kind===kind) {
          const confirmation = node('div','inline-confirm');
          confirmation.append(paragraph(`为 ${text(agent.label) || kind} 安装官方状态集成？这会修改下面的集成路径，不会读取会话正文。完成后需要重新启动这个 Agent。`,''));
          for (const path of (Array.isArray(agent.targetPaths)?agent.targetPaths:[]).slice(0,8)) confirmation.append(node('small','setup-path',path));
          const actions = node('div','detail-actions'); actions.append(button('确认安装集成','otty-setup-apply',{className:'primary-button',id:kind,key:`otty-apply-${kind}`}),button('暂不修改','otty-setup-cancel',{key:'otty-cancel'})); confirmation.append(actions); row.append(confirmation);
        }
      } else if (agent.nextAction==='open_otty_settings') row.append(paragraph('请在 Otty 设置 → Agents 中检查该 Agent 的官方集成。haochen 不会自动重写它的配置。','field-help'));
      // CW（codewiz-cc）驱动的 Claude 会话有独立配置目录；官方钩子装不到那里，需要单独指引。
      if (agent.cwNote) row.append(paragraph(text(agent.cwNote,300),'field-help'));
      if (agent.needsRestart) row.append(paragraph('集成已有配置，但当前会话可能尚未加载；重启对应 Agent 后再检查。','field-help'));
      help.append(row);
    }
    const unrecognized = list(diagnosis.unrecognizedNames,8);
    if (unrecognized.length || Number(diagnosis.unrecognizedAgents) > 0) {
      const row = node('div','agent-diagnostic');
      const names = unrecognized.map(n=>text(n,40)).filter(Boolean).join('、');
      row.append(node('strong','',`未识别的 Agent（${Number(diagnosis.unrecognizedAgents)||unrecognized.length} 个）`));
      row.append(paragraph(names?`已发现终端：${names}。haochen 不会猜测它的类型，也不会替它改写配置。`:'已发现终端，但无法识别其 Agent 类型。',''));
      row.append(paragraph('请在 Otty 设置 → Agents 中为它安装官方状态集成，再重启该会话。','field-help'));
      help.append(row);
    }
    card.append(help);
  }
  function renderBrowserConnection(connector,card) {
    const setup = record(connector.setup), help = node('div','connector-help'), steps = node('ol','connection-steps');
    if (setup.message) help.append(paragraph(setup.message,''));
    const configuredId = /^[a-p]{32}$/.test(text(setup.extensionId)) ? setup.extensionId : '';
    for (const step of browserSteps(connector)) {
      const row = node('li',`connection-step${step.done?' is-complete':''}${step.current?' is-current':''}`); row.dataset.stage=step.stage;
      const mark = node('span','step-mark',step.done?'':String(steps.childNodes.length+1)); if (step.done) mark.append(icon('check'));
      const copy = node('div','step-copy'); copy.append(node('strong','',step.title));
      const state = step.done ? '已确认' : step.status==='attention' ? '需要处理' : step.current ? '下一步' : '待确认';
      copy.append(node('span','step-status',state));
      if (step.stage==='extension') {
        copy.append(paragraph('打开 Chrome 扩展页，启用开发者模式，选择“加载已解压的扩展程序”，然后选取这里的文件夹。',''),button('打开扩展文件夹','browser-folder',{className:'text-button',icon:'folder',key:'browser-folder'}));
      } else if (step.stage==='bridge') {
        copy.append(paragraph(configuredId?'使用本包扩展的固定 ID 配置本机连接。若你另行加载了其他版本，可填写其扩展 ID。':'将 Chrome 扩展页显示的 ID 粘贴到这里，只为这个扩展配置本机连接。',''));
        const extension = input('extension-id',ui.extensionId || configuredId,'32 位 Chrome 扩展 ID',32); extension.className='extension-input'; extension.autocomplete='off'; extension.spellcheck=false; extension.setAttribute('aria-label','Chrome 扩展 ID');
        copy.append(extension,button(step.done?'重新配置本机连接':'配置本机连接','browser-install',{className:'secondary-button',icon:'link',key:'browser-install'}));
      } else if (step.stage==='authorization') copy.append(paragraph('回到想追踪的网页，点击 haochen 扩展，允许读取当前网站，再选择“追踪当前页面”。每个站点单独授权，不读取其他页面。',''));
      else copy.append(paragraph(step.done?'已收到授权页面的真实内容；之后的变化会出现在应用动态。':'等待扩展送来首次读取结果。只有实际收到页面内容后，这一步才会完成。',''),button('重新检查连接','connection-refresh',{className:'text-button',icon:'refresh',key:'browser-refresh'}));
      row.append(mark,copy); steps.append(row);
    }
    help.append(steps); card.append(help);
  }

  async function ask(payload, el) { snapshotView(); await perform('askHaochen',payload,el); }
  app.addEventListener('click',async event => {
    const el = event.target.closest('[data-action]'); if (!el || el.disabled) return;
    const action = el.dataset.action, id = el.dataset.id;
    if (action === 'collapse') { snapshotView(); perform('collapse',{},el); }
    else if (action === 'refresh') perform('refresh',{},el);
    else if (action === 'retry-ready') perform('ready',{},el);
    else if (action === 'connections') openModal('connections',null,el);
    else if (action === 'auth-dot') {
      const connector = ui.state.connectors.find(c => c.id === id);
      // One click: enable a not-yet-enabled source (calendar → system prompt fires,
      // 微信 enables); for connected or step-requiring sources, open its guide.
      if (connector && !connector.enabled) perform('connectorEnable',{id,enabled:true},el);
      else openModal('connections',null,el,{focusConnector:id});
    }
    else if (action === 'connector-open') openModal('connections',null,el,{focusConnector:id});
    else if (action === 'report') openModal('report',null,el);
    else if (action === 'event') { if (ui.state.events.some(item=>text(item.id)===id)) openModal('event',id,el); else toast('这条动态已移出当前列表，请查看最新动态。'); }
    else if (action === 'calendar') { if ([...ui.state.calendar,...ui.state.events].some(item=>text(item.id)===id)) openModal('calendar',id,el); else toast('这条日程已不在当前列表。'); }
    else if (action === 'track') { if (ui.state.tracks.some(item=>text(item.id)===id)) openModal('track',id,el); else toast('这个事项已移除，其他事项没有变化。'); }
    else if (action === 'track-new') openModal('edit',null,el);
    else if (action === 'track-edit') openModal('edit',id,el);
    else if (action === 'modal-back') closeModal();
    else if (action === 'event-dismiss') {
      // 忽略这条动态（B-11）：从列表移除并持久化；浏览器来源同时停止追踪。
      perform('eventDismiss',{eventId:id},el);
    }
    else if (action === 'event-source') {
      // Going to handle it counts as handled: mark read so a Hi message drops
      // from the feed when you come back (other sources just lose the dot).
      const ev = ui.state.events.find(e => e.id === id || e.sourceId === id);
      if (ev && ev.attentionVersion) perform('eventRead',{eventId:ev.id, version:ev.attentionVersion});
      perform('openSource',{eventId:id},el);
    }
    else if (action === 'evidence-open' && safeURL(el.dataset.url)) perform('openSource',{url:safeURL(el.dataset.url)},el);
    else if (action === 'file-open') perform('openSource',{fileId:id},el);
    else if (action === 'file-add') perform('pickFolder',{},el);
    else if (action === 'file-remove') perform('fileRemove',{id},el,'已移除快捷入口，原文件未改动。');
    else if (action === 'event-ask') ask({eventId:id},el);
    else if (action === 'track-ask') ask({trackId:id},el);
    else if (action === 'report-ask') ask({reportDate:top()?.date || localDate()},el);
    else if (action === 'ask-global') ask({},el);
    else if (action === 'track-refresh') perform('trackRefresh',{id},el);
    else if (action === 'track-pause') { const track = ui.state.tracks.find(item => text(item.id)===id); if (track) perform('trackPause',{id,paused:!track.paused},el); }
    else if (action === 'track-delete-confirm') { top().confirm = true; top().focusKey = 'confirm-delete'; renderModal(true); }
    else if (action === 'confirm-cancel') { snapshotView(); top().confirm = false; top().focusKey = 'track-delete-confirm'; renderModal(false,false); }
    else if (action === 'track-delete') { const view = top(); const result = await perform('trackDelete',{id},el,'事项已移除，原始来源未删除。'); if (result && top()===view) { view.confirm=false; closeModal(); } }
    else if (action === 'source-add' || action === 'source-remove') {
      snapshotView(); const draft = getDraft(top()); if (action === 'source-add' && draft.sources.length<12) draft.sources.push({type:'url',label:'',locator:''}); else if (action === 'source-remove') draft.sources.splice(Number(id),1);
      top().focusKey = action === 'source-add' ? `source-locator-${draft.sources.length-1}` : 'source-add'; renderModal(false,false);
    }
    else if (action === 'report-refresh') loadReport(top()?.date || localDate(),true);
    else if (action === 'connector-toggle') {
      const connector = ui.state.connectors.find(item => text(item.id)===id); if (!connector) return;
      if (connector.status === 'connected') {
        const result = await perform('connectorEnable',{id,enabled:false},el); if (result && top()?.kind==='connections') renderModal(false);
      } else {
        // 未连接：确保启用 → 尽力完成连接（浏览器桥接如已知扩展 ID 就一并装）→ 重新探测。
        if (!connector.enabled) await perform('connectorEnable',{id,enabled:true},el);
        if (id === 'browser') { const ext = text($('[name="extension-id"]')?.value || ui.extensionId,100).trim(); if (/^[a-p]{32}$/.test(ext)) await perform('browserInstall',{extensionId:ext},el); }
        const result = await perform('refresh',{},el); if (result && top()?.kind==='connections') renderModal(false);
      }
    }
    else if (action === 'palette') {
      const palette = el.dataset.palette; if (!Object.hasOwn(PALETTES,palette)) return;
      const result = await perform('settingsUpdate',{palette},el); if (result && top()?.kind==='connections') renderModal(false);
    }
    else if (action === 'settings') { snapshotView(); perform('openSettings',{},el); }
    else if (action === 'browser-folder') perform('browserExtensionFolder',{},el);
    else if (action === 'connection-refresh') perform('refresh',{},el);
    else if (action === 'wechat-open') perform('openSource',{connectorId:'wechat'},el);
    else if (action === 'wechat-permission') perform('openSource',{permission:'accessibility'},el);
    else if (action === 'otty-check') {
      if (top()?.kind!=='connections') openModal('connections',null,el,{focusConnector:'otty'});
      perform('ottyCheck',{},el);
    }
    else if (action === 'otty-setup-plan') {
      const view=top(), result=await perform('ottySetup',{agentKind:id},el);
      if (result && top()===view) { snapshotView(); view.ottyPlan={kind:id}; view.focusKey=`otty-apply-${id}`; renderModal(false,false); requestAnimationFrame(()=>findFocus(view.focusKey,body)?.scrollIntoView({block:'center',behavior:'auto'})); }
    }
    else if (action === 'otty-setup-apply' && top()?.ottyPlan?.kind===id) {
      const view=top(), result=await perform('ottySetup',{agentKind:id,confirmed:true},el);
      if (result && top()===view) { view.ottyPlan=null; renderModal(false); }
    }
    else if (action === 'otty-setup-cancel') { top().ottyPlan=null; renderModal(false); }
    else if (action === 'calendar-list') {
      ui.calendarLoading=true; ui.calendarError=''; renderModal(false);
      clearTimeout(ui.calendarTimer); ui.calendarTimer=setTimeout(()=>{ ui.calendarLoading=false; ui.calendarError='日历列表暂未返回，请确认权限后重试。'; if (top()?.kind==='connections') renderModal(false); },20000);
      const result = await perform('calendarList',{},el); if (!result) { clearTimeout(ui.calendarTimer); ui.calendarLoading=false; if(top()?.kind==='connections')renderModal(false); }
    }
    else if (action === 'calendar-save') perform('calendarSelect',{ids:[...(ui.calendarSelection||[])].filter(id=>ui.calendars?.some(item=>text(item.id)===id))},el,'日历选择已保存。');
    else if (action === 'browser-install') {
      const extensionId = text($('[name="extension-id"]')?.value || ui.extensionId,100).trim(); if (!/^[a-p]{32}$/.test(extensionId)) { toast('请粘贴 Chrome 扩展页显示的 32 位扩展 ID（仅包含 a–p 字母）。'); return; }
      const result = await perform('browserInstall',{extensionId},el,'本机连接配置已保存，请回到 Chrome 授权需要读取的站点。'); if (result && top()?.kind==='connections') renderModal(false);
    }
  });
  $('#float-scrim').addEventListener('click',closeModal);
  overview.addEventListener('pointerdown',event => { if (event.isPrimary && event.button === 0) overviewPointer = true; },true);
  body.addEventListener('pointerdown',event => { if (event.isPrimary && event.button === 0) modalPointer = true; },true);
  global.addEventListener('pointerup',releaseOverviewPointer,true);
  global.addEventListener('pointercancel',releaseOverviewPointer,true);
  global.addEventListener('blur',releaseOverviewPointer);
  global.addEventListener('pointerup',releaseModalPointer,true);
  global.addEventListener('pointercancel',releaseModalPointer,true);
  global.addEventListener('blur',releaseModalPointer);
  app.addEventListener('submit',event => { if (event.target.id === 'track-form') { event.preventDefault(); if (!ui.composing && performance.now()-ui.compositionEnd>80) saveDraft(event.target); } });
  app.addEventListener('input',event => {
    if (event.target.closest('#track-form')) { readDraft(); const error = $('#track-form-error'); if (error) error.textContent = ''; }
    if (event.target.name === 'extension-id') ui.extensionId = event.target.value;
  });
  app.addEventListener('change',async event => {
    const el = event.target;
    if (el.closest('#track-form')) {
      readDraft(); if (el.name.startsWith('source-type-')) { snapshotView(); const index = el.name.split('-').at(-1), source = getDraft(top()).sources[Number(index)]; source.locator=''; top().focusKey=`source-locator-${index}`; renderModal(false,false); }
    }
    if (el.name === 'report-date' && top()?.kind === 'report') {
      if (!validDate(el.value) || el.value > localDate()) { el.value = top().date; return; }
      snapshotView(); const date = el.value; top().date = date; top().scroll = 0; top().openDetails = []; top().focusKey = 'report-date'; renderModal(false,false); loadReport(date);
    }
    if (['dock','motion'].includes(el.name)) {
      const key = el.name, value = el.value; const result = await perform('settingsUpdate',{[key]:value},el); if (!result) el.value = ui.state.settings[key];
    }
    if (el.dataset.calendarId) { if (!ui.calendarSelection) ui.calendarSelection=new Set(); if (el.checked) ui.calendarSelection.add(el.dataset.calendarId); else ui.calendarSelection.delete(el.dataset.calendarId); }
  });
  app.addEventListener('compositionstart',() => { ui.composing = true; },true);
  app.addEventListener('compositionend',() => { ui.composing = false; ui.compositionEnd = performance.now(); },true);
  function escape() {
    if (!mayEscape(ui.composing,ui.compositionEnd,performance.now())) return false;
    if (top()) closeModal(); else perform('collapse'); return true;
  }
  global.haochenNativeEscape = escape;
  document.addEventListener('keydown',event => {
    if (event.key === 'Escape' && !event.isComposing && event.keyCode!==229) { if (escape()) { event.preventDefault(); event.stopPropagation(); } return; }
    if (event.key === 'Tab' && top()) {
      const focusable = [...win.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary')].filter(el => el.getClientRects().length>0);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && (!win.contains(document.activeElement) || document.activeElement===first)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (!win.contains(document.activeElement) || document.activeElement===last)) { event.preventDefault(); first?.focus(); }
    }
  },true);
  document.addEventListener('dragover',event => { if (Array.from(event.dataTransfer?.types || []).includes('Files')) { event.preventDefault(); event.dataTransfer.dropEffect='copy'; $('#file-shelf').classList.add('is-drop'); } });
  document.addEventListener('dragleave',event => { if (!event.relatedTarget) $('#file-shelf').classList.remove('is-drop'); });
  document.addEventListener('drop',event => { event.preventDefault(); $('#file-shelf').classList.remove('is-drop'); /* Native NSDraggingDestination owns file URLs and persists the actual shortcut. */ });
  global.addEventListener('resize',() => { cancelAnimationFrame(ui.resizeFrame); ui.resizeFrame = requestAnimationFrame(() => { if (top()) morph(true,false); }); });
  global.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',() => { if (top()) morph(true,false); });
  global.addEventListener('pagehide',() => { clearTimeout(ui.calendarTimer); bridge.destroy(); },{once:true});
  global.HaochenIcons.hydrate(document); renderOverview();
  (function clock() {
    const t = document.getElementById('clock-time'), d = document.getElementById('clock-date');
    const wk = ['周日','周一','周二','周三','周四','周五','周六'];
    const tick = () => {
      const n = new Date();
      if (t) t.textContent = `${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}`;
      if (d) d.textContent = `${n.getMonth()+1}月${n.getDate()}日 ${wk[n.getDay()]}`;
    };
    tick(); global.setInterval(tick, 1000);
  })();
  bridge.request('ready').catch(error => { ui.error = errorText(error); renderService(); });
})(globalThis);
