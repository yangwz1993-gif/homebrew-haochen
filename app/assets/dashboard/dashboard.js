/* haochen 0.5 local dashboard. External content is rendered as text, never HTML.
 * Outbound: {v:1,id,action,payload} -> window.webkit.messageHandlers.haochen.
 * Inbound: window.haochenReceive({id?,ok?,error?,state?}). State is authoritative;
 * the UI never invents successful connections, tracking conclusions or reports.
 */
(function (global) {
  'use strict';
  const PALETTES = Object.freeze({sage:'浅雾绿', stone:'暖白石墨', mist:'冷白雾蓝', carbon:'中性炭灰'});
  const FREQUENCIES = Object.freeze({manual:'仅手动', quarter:'每 15 分钟', hourly:'每小时', daily:'每天'});
  const ACTIONS = new Set(['ready','refresh','openSource','trackCreate','trackUpdate','trackPause','trackRefresh','trackDelete','reportGet','pickFolder','fileRemove','askHaochen','openSettings','collapse','connectorEnable','settingsUpdate','browserInstall','browserExtensionFolder','calendarList','calendarSelect']);
  const STATUS = Object.freeze({connected:'已连接', disabled:'未开启', permission_required:'需要授权', limited:'能力受限', partial:'内容不完整', unavailable:'暂不可用', not_running:'应用未运行', error:'检查失败', failed:'检查失败', running:'进行中', processing:'处理中', working:'进行中', busy:'检查中', checking:'检查中', awaiting:'等待确认', waiting:'等待确认', needs_attention:'需要关注', completed:'已完成', complete:'已完成', success:'已更新', changed:'有变化', unchanged:'未发现变化', idle:'就绪', paused:'已暂停', pending:'等待检查', unknown:'状态未知', unavailable_source:'来源不可用', stale:'等待更新', unsupported:'暂不支持', warning:'需要关注', ready:'就绪'});
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
    return {events:list(raw.events), tracks:list(raw.tracks,200), files:list(raw.files,64), connectors:list(raw.connectors,50), reports:list(raw.reports,370), calendar:list(raw.calendar,100), settings:{...settings, palette:Object.hasOwn(PALETTES,settings.palette) ? settings.palette : 'sage', dock:settings.dock === 'notch' ? 'notch' : 'side', motion:settings.motion === 'reduced' ? 'reduced' : 'system'}, updatedAt:text(raw.updatedAt), error:text(raw.error), incomplete:raw.incomplete === true};
  }
  function localDate(date = new Date()) {
    return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  }
  function validDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(`${value}T12:00:00`);
    return Number.isFinite(date.getTime()) && localDate(date) === value;
  }
  function statusLabel(value) { return STATUS[text(value,60)] || '状态未知'; }
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
    if (/wechat|微信|飞书|feishu|lark/.test(name)) return 'message';
    if (/file|folder|文件/.test(name)) return 'folder';
    return 'bell';
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
  const core = {PALETTES,FREQUENCIES,ACTIONS,normalizeState,validateTrackDraft,safeURL,localDate,validDate,statusLabel,floatingGeometry,mayEscape,NativeBridge};
  if (typeof module === 'object' && module.exports) module.exports = core;
  global.HaochenDashboardCore = core;
  if (typeof document === 'undefined') return;

  const $ = selector => document.querySelector(selector);
  const app = $('#dashboard'), overview = $('#overview'), layer = $('#float-layer'), win = $('#float-window'), content = $('#float-content'), body = $('#float-body');
  const icon = name => global.HaochenIcons.icon(name);
  const ui = {state:normalizeState({}),connected:false,received:false,error:'',stack:[],phase:'closed',animations:[],animationId:0,drafts:new Map(),busy:new Set(),reportErrors:new Map(),reportLoading:new Set(),composing:false,compositionEnd:0,toastTimer:null,extensionId:'',resizeFrame:0,calendars:null,calendarSelection:null,calendarLoading:false,calendarError:'',calendarTimer:null};
  const overviewSignatures = new Map();
  let overviewPointer = false, overviewPending = false, overviewFrame = 0;
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
      if (top() && (top().kind !== 'edit' || catalogChanged)) renderModal(false);
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
  global.haochenVisibilityChanged = visible => { document.body.dataset.suspended = visible ? 'false' : 'true'; if (!visible && top()) closeAll(false); };

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
    applyAppearance(); renderEvents(); renderTracks(); renderCalendar(); renderFiles(); renderService();
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
    const events = ui.state.events.filter(item => item.type !== 'calendar' && item.kind !== 'calendar');
    const connected = ui.state.connectors.some(item => item.status === 'connected');
    if (!overviewChanged('events',[events.length,events.length ? null : [ui.received,connected],events.slice(0,30).map(item=>[item.id,item.source,item.sourceId,item.title,item.summary || item.description,item.status,timeLabel(item.occurredAt || item.updatedAt)])])) return;
    const target = $('#event-list'); target.replaceChildren();
    $('#event-count').textContent = events.length ? `${events.length} 条` : '';
    if (!events.length) {
      target.append(empty(ui.received ? connected ? '此刻，没有新的动态。' : '给重要的消息，留一个位置。' : '正在连接你的本机服务', ui.received ? connected ? '已连接来源的真实变化会出现在这里。不打扰，也不遗漏。' : '连接 Agent、浏览器或日历，让真实变化自然浮现。' : '正在读取本地连接状态，不会载入示例消息。','bell',ui.received ? 'connections' : null,'连接应用')); return;
    }
    for (const event of events.slice(0,30)) {
      const id = text(event.id,200), el = button('','event',{className:'event-card',id,key:`event-${id}`});
      const kind = sourceKind(event.source || event.sourceId), glyph = node('span',`source-glyph${kind === 'terminal' ? ' agent' : ''}`); glyph.append(icon(kind));
      const copy = node('span','event-copy'); copy.append(node('span','event-title',event.title || '未命名动态'),node('span','event-summary',[text(event.source),text(event.summary || event.description)].filter(Boolean).join(' · ')));
      const meta = node('span','event-meta'), label = node('span'); label.append(node('span',`status-dot ${['error','failed'].includes(event.status) ? 'error' : ['running','processing','working','checking'].includes(event.status) ? 'running' : ['waiting','awaiting','needs_attention','warning'].includes(event.status) ? 'warning' : ''}`),document.createTextNode(statusLabel(event.status)));
      meta.append(label,node('span','',timeLabel(event.occurredAt || event.updatedAt))); el.append(glyph,copy,meta); target.append(el);
    }
    if (events.length > 30) target.append(paragraph(`先展示最近 30 条，共 ${events.length} 条；日报中可回顾今日变化。`,'compact-empty'));
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
    if (top().confirm) { top().confirm = false; renderModal(false); return; }
    snapshotView(); const closed = ui.stack.pop();
    if (top()) renderModal(true); else morph(false,true,closed);
  }
  function closeAll(animate = true) {
    if (!top()) return; snapshotView(); const first = ui.stack[0]; ui.stack = []; morph(false,animate,first);
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
    for (const detail of body.querySelectorAll('details')) if (current.openDetails?.includes(detail.dataset.evidence)) detail.open = true;
    morph(true,animate); body.scrollTop = current.scroll || 0;
    requestAnimationFrame(() => {
      if (top() !== current || ui.phase === 'closing') return;
      body.scrollTop = current.scroll || 0;
      const focus = current.focusKey && findFocus(current.focusKey,body);
      (focus || $('[data-focuskey="modal-close"]')).focus({preventScroll:true});
    });
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
    const event = ui.state.events.find(item => text(item.id) === view.id);
    if (!event) { body.append(empty('这条动态已不在当前列表','它可能已过期或被来源移除。你可以返回总览查看最新动态。','bell')); return; }
    body.append(node('div','eyebrow',text(event.source) || '应用动态'),node('h1','',event.title || '未命名动态'),metadata([['clock',timeLabel(event.occurredAt || event.updatedAt,true)],['info',statusLabel(event.status)]]));
    if (event.incomplete) body.append(notice('当前内容不完整。以下仅展示已经取得的信息，不能据此认定没有其他变化。','warning'));
    if (event.error) body.append(notice(event.error,'error'));
    body.append(paragraph(event.summary || event.description || '来源尚未提供这条动态的摘要。','body-copy'));
    const actions = node('div','detail-actions'); actions.append(button('打开来源','event-source',{icon:'arrow-up-right',id:view.id,key:'event-source'}),button('问 haochen','event-ask',{className:'primary-button',icon:'sparkles',id:view.id,key:'event-ask'})); body.append(actions);
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
    const event = ui.state.calendar.find(item => text(item.id) === view.id) || ui.state.events.find(item => text(item.id) === view.id);
    if (!event) { body.append(paragraph('这条日程已不在当前列表。')); return; }
    body.append(node('div','eyebrow','日历'),node('h1','',event.title || '未命名日程'),metadata([['clock',`${timeLabel(event.startAt || event.occurredAt,true)}${event.endAt?' — '+timeLabel(event.endAt):''}`]]));
    if (event.location) body.append(paragraph(event.location,'body-copy')); if (event.summary || event.description) body.append(paragraph(event.summary || event.description,'body-copy'));
    const actions = node('div','detail-actions'); actions.append(button('打开日程来源','event-source',{icon:'arrow-up-right',id:view.id})); body.append(actions); renderEvidence(event.evidence,body);
  }
  function renderConnections() {
    body.append(node('h1','','让常用应用连在一起'),paragraph('每个连接单独开启。未授权、不可用与能力受限都会如实显示；不会读取未经允许的内容。'));
    if (!ui.state.connectors.length) body.append(notice('还没有取得连接列表，请检查本机服务是否正常运行。','warning'));
    for (const connector of ui.state.connectors) {
      const id = text(connector.id,200), card = node('section','connector-card'), header = node('div','connector-heading');
      const name = node('h2','connector-name'); name.append(icon(sourceKind(id)),document.createTextNode(text(connector.name) || id)); header.append(name,node('span','connector-status',statusLabel(connector.status))); card.append(header);
      card.append(paragraph(connector.summary || connector.description || '此来源尚未提供连接说明。'));
      if (connector.error) card.append(paragraph(connector.error,'form-error'));
      const actions = node('div','detail-actions');
      if (!/lark|feishu|wechat/.test(id) && (connector.enabled || connector.status!=='unsupported')) actions.append(button(connector.enabled?'关闭连接':connector.status === 'permission_required'?'开启并授权':'开启连接','connector-toggle',{className:connector.enabled?'secondary-button':'primary-button',id,key:`connector-${id}`}));
      if (safeURL(connector.helpUrl)) { const help = button('连接帮助','evidence-open',{icon:'arrow-up-right',className:'text-button'}); help.dataset.url = safeURL(connector.helpUrl); actions.append(help); }
      if (actions.childNodes.length) card.append(actions);
      if (/otty/.test(id)) { const help = node('div','connector-help'); help.append(paragraph('在 Otty 设置 → Agents 中安装官方集成，再重新启动需要追踪的 Agent 会话。haochen 只展示实际收到的状态，不会自动修改 Otty 配置。','')); card.append(help); }
      if (/wechat/.test(id)) card.append(paragraph('微信仅提供当前可取得的受限状态；这不代表可以读取全部消息或获得完整通知。','connector-help'));
      if (/lark|feishu/.test(id)) card.append(paragraph('飞书深度连接未纳入 0.5，本版本不会标记为已接通。','connector-help'));
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
        const help = node('div','connector-help'); help.append(paragraph('Chrome 使用本机扩展连接。每个站点单独授权，未授权页面不会被读取。先打开扩展文件夹，在 Chrome 扩展页开启开发者模式并加载该目录，再复制扩展 ID。',''));
        help.append(button('打开扩展文件夹','browser-folder',{className:'text-button',icon:'folder',key:'browser-folder'}));
        const extension = input('extension-id',ui.extensionId,'粘贴 32 位 Chrome 扩展 ID',32); extension.className = 'extension-input'; extension.autocomplete = 'off'; extension.spellcheck = false; extension.setAttribute('aria-label','Chrome 扩展 ID'); help.append(extension,button('完成本机连接','browser-install',{className:'secondary-button',icon:'link',key:'browser-install'})); card.append(help);
      }
      body.append(card);
    }
    const appearance = section('看起来，像你喜欢的样子');
    const palettes = node('div','palette-grid'); for (const [key,label] of Object.entries(PALETTES)) { const el = button('','palette',{className:'palette-button',key:`palette-${key}`}); el.dataset.palette = key; el.setAttribute('aria-pressed',String(ui.state.settings.palette===key)); el.append(node('span','palette-swatch'),node('span','',label)); palettes.append(el); } appearance.append(palettes);
    const dock = node('div','settings-row'), dockLabel = node('label','','收起位置'), dockSelect = select('dock',{side:'屏幕侧边',notch:'顶部入口'},ui.state.settings.dock); dockLabel.htmlFor='dock-setting'; dockSelect.id='dock-setting'; dock.append(dockLabel,dockSelect);
    const motion = node('div','settings-row'), motionLabel = node('label','','界面动效'), motionSelect = select('motion',{system:'跟随系统',reduced:'减少动态效果'},ui.state.settings.motion); motionLabel.htmlFor='motion-setting'; motionSelect.id='motion-setting'; motion.append(motionLabel,motionSelect); appearance.append(dock,motion); body.append(appearance);
    const model = section('对话与模型'); model.append(paragraph('继续使用你已配置的模型和本机安全存储。这里不会显示 API Key。'),button('打开 haochen 设置','settings',{className:'secondary-button',icon:'settings',key:'model-settings'})); body.append(model);
  }

  async function ask(payload, el) { const result = await perform('askHaochen',payload,el); if (result) closeAll(true); }
  app.addEventListener('click',async event => {
    const el = event.target.closest('[data-action]'); if (!el || el.disabled) return;
    const action = el.dataset.action, id = el.dataset.id;
    if (action === 'collapse') { closeAll(false); perform('collapse',{},el); }
    else if (action === 'refresh') perform('refresh',{},el);
    else if (action === 'retry-ready') perform('ready',{},el);
    else if (action === 'connections') openModal('connections',null,el);
    else if (action === 'report') openModal('report',null,el);
    else if (action === 'event') { if (ui.state.events.some(item=>text(item.id)===id)) openModal('event',id,el); else toast('这条动态已移出当前列表，请查看最新动态。'); }
    else if (action === 'calendar') { if ([...ui.state.calendar,...ui.state.events].some(item=>text(item.id)===id)) openModal('calendar',id,el); else toast('这条日程已不在当前列表。'); }
    else if (action === 'track') { if (ui.state.tracks.some(item=>text(item.id)===id)) openModal('track',id,el); else toast('这个事项已移除，其他事项没有变化。'); }
    else if (action === 'track-new') openModal('edit',null,el);
    else if (action === 'track-edit') openModal('edit',id,el);
    else if (action === 'modal-back') closeModal();
    else if (action === 'event-source') perform('openSource',{eventId:id},el);
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
      const result = await perform('connectorEnable',{id,enabled:!connector.enabled},el); if (result && top()?.kind==='connections') renderModal(false);
    }
    else if (action === 'palette') {
      const palette = el.dataset.palette; if (!Object.hasOwn(PALETTES,palette)) return;
      const result = await perform('settingsUpdate',{palette},el); if (result && top()?.kind==='connections') renderModal(false);
    }
    else if (action === 'settings') { const result = await perform('openSettings',{},el); if (result) closeAll(false); }
    else if (action === 'browser-folder') perform('browserExtensionFolder',{},el);
    else if (action === 'calendar-list') {
      ui.calendarLoading=true; ui.calendarError=''; renderModal(false);
      clearTimeout(ui.calendarTimer); ui.calendarTimer=setTimeout(()=>{ ui.calendarLoading=false; ui.calendarError='日历列表暂未返回，请确认权限后重试。'; if (top()?.kind==='connections') renderModal(false); },20000);
      const result = await perform('calendarList',{},el); if (!result) { clearTimeout(ui.calendarTimer); ui.calendarLoading=false; if(top()?.kind==='connections')renderModal(false); }
    }
    else if (action === 'calendar-save') perform('calendarSelect',{ids:[...(ui.calendarSelection||[])].filter(id=>ui.calendars?.some(item=>text(item.id)===id))},el,'日历选择已保存。');
    else if (action === 'browser-install') {
      const extensionId = text(ui.extensionId,100).trim(); if (!/^[a-p]{32}$/.test(extensionId)) { toast('请粘贴 Chrome 扩展页显示的 32 位扩展 ID（仅包含 a–p 字母）。'); return; }
      const result = await perform('browserInstall',{extensionId},el,'本机连接配置已保存，请回到 Chrome 授权需要读取的站点。'); if (result && top()?.kind==='connections') renderModal(false);
    }
  });
  $('#float-scrim').addEventListener('click',closeModal);
  overview.addEventListener('pointerdown',event => { if (event.isPrimary && event.button === 0) overviewPointer = true; },true);
  global.addEventListener('pointerup',releaseOverviewPointer,true);
  global.addEventListener('pointercancel',releaseOverviewPointer,true);
  global.addEventListener('blur',releaseOverviewPointer);
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
  bridge.request('ready').catch(error => { ui.error = errorText(error); renderService(); });
})(globalThis);
