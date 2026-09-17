/* Only explicit page selections enter sources. Never enumerate all browser tabs. */
const HOST = "com.haochen.browser";
const MAX_SOURCES = 32;
const MAX_CONTENT = 60000;
const TRACK_PERMISSION_TIMEOUT = 60000;
let sources = {}, clientId, port = null, connecting = false, connected = false;
let bridgeMessage = "尚未连接 haochen。请先在应用中安装桥接。";
let bridgeReason = "not_connected", connectionTimer = null;
let pendingForgets = [];
let sessionId = null, focusBusy = false;
// Ephemeral, one-at-a-time user intent. Never persisted: browser/worker restart
// must not resurrect an old permission request or select another active page.
let pendingTrack = null, trackingMessage = "";
const ready = chrome.storage.local.get(["sources", "clientId", "pendingForgets"]).then(saved => {
  sources = saved.sources || {};
  pendingForgets = saved.pendingForgets || [];
  clientId = saved.clientId || crypto.randomUUID();
  return chrome.storage.local.set({clientId});
});

function pageURL(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.href.length > 8192) {
    throw new Error("仅支持你明确选择的 HTTP(S) 网页，不能追踪浏览器设置或扩展页面。");
  }
  return url.href;
}
function originPattern(url) { return new URL(url).origin + "/*"; }
function cancelTrackIntent(intent, message) {
  if (!intent || intent.cancelled || intent.consumed) return;
  intent.cancelled = true;
  clearTimeout(intent.timer);
  if (pendingTrack === intent) { pendingTrack = null; trackingMessage = message; }
  intent.resolveCancel?.({cancelled: true, message});
}
function checkTrackIntent(intent) {
  if (intent.cancelled || pendingTrack !== intent || Date.now() >= intent.expiresAt) {
    throw new Error("这次追踪选择已取消或超时。请重新选择当前页面。");
  }
  if (!connected) throw new Error("本机连接已断开，未加入追踪。请重新连接后再试。");
}
async function selectedTab(intent) {
  checkTrackIntent(intent);
  let tab;
  try { tab = await chrome.tabs.get(intent.tabId); }
  catch (_) { throw new Error("授权期间标签页已关闭，未加入追踪。"); }
  checkTrackIntent(intent);
  if (tab.incognito || tab.id !== intent.tabId || tab.windowId !== intent.windowId ||
      pageURL(tab.url) !== intent.url || tab.discarded || tab.frozen) {
    throw new Error("授权期间页面或窗口已变化，未加入追踪。请重新选择。");
  }
  return tab;
}
async function finishTrackRequest(intent, permission) {
  try {
    const outcome = await Promise.race([permission.then(allowed => ({allowed})), intent.cancellation]);
    if (outcome.cancelled) return {ok: false, message: outcome.message};
    if (!outcome.allowed) {
      cancelTrackIntent(intent, "你没有授权该网站，未读取页面、未加入追踪。");
      return {ok: false, message: trackingMessage};
    }
    await ready;
    await selectedTab(intent);
    if (!await chrome.permissions.contains({origins: [originPattern(intent.url)]})) {
      throw new Error("网站权限已撤回，未加入追踪。");
    }
    const previous = Object.values(sources).find(source => source.url === intent.url);
    if (!previous && Object.keys(sources).length >= MAX_SOURCES) throw new Error("最多追踪 32 个页面，请先移除不再需要的来源。");
    if (previous) await stopObserver(previous);
    // Revalidate after every asynchronous preparation, immediately before the
    // atomic binding. A grant is not permission to follow a different URL/tab.
    if (!await chrome.permissions.contains({origins: [originPattern(intent.url)]})) {
      throw new Error("网站权限已撤回，未加入追踪。");
    }
    const tab = await selectedTab(intent);
    const source = {id: previous?.id || crypto.randomUUID(), url: intent.url,
      title: (tab.title || intent.url).slice(0, 500), tabId: intent.tabId, windowId: intent.windowId,
      status: "reading", checkedAt: Date.now()};
    // Consume the intent before saving/reading. Later navigation is handled as
    // a changed fixed source, never as a new implicit page selection.
    intent.consumed = true; clearTimeout(intent.timer); pendingTrack = null;
    sources[source.id] = source;
    trackingMessage = "已加入追踪。仅读取你选择的固定页面；切换网址不会自动改读。";
    for (const old of Object.values(sources)) {
      if (old.tabId === source.tabId && old.id !== source.id) await setStatus(old, "target_changed");
    }
    await save(); await refresh(source);
    return {ok: true};
  } catch (error) {
    const message = error instanceof Error ? error.message : "网站授权未完成，未加入追踪。";
    cancelTrackIntent(intent, message);
    return {ok: false, message};
  }
}
function requestTracking(message, sender) {
  if (sender.url !== chrome.runtime.getURL("popup.html")) return Promise.resolve({ok: false});
  if (!connected) return Promise.resolve({ok: false, message: "请先完成 haochen 本机连接，再选择网页。"});
  if (pendingTrack) return Promise.resolve({ok: false, message: "已有网站授权等待处理；请完成或取消后重试。"});
  let url;
  try {
    url = pageURL(message.url);
    if (!Number.isInteger(message.tabId) || message.tabId < 0 ||
        !Number.isInteger(message.windowId) || message.windowId < 0) throw new Error("无效的页面选择。");
  } catch (_) { return Promise.resolve({ok: false, message: "页面选择无效，请重新打开扩展后选择。"}); }
  const intent = {id: crypto.randomUUID(), tabId: message.tabId, windowId: message.windowId,
    url, expiresAt: Date.now() + TRACK_PERMISSION_TIMEOUT, cancelled: false, consumed: false};
  intent.cancellation = new Promise(resolve => { intent.resolveCancel = resolve; });
  pendingTrack = intent;
  trackingMessage = "正在等待网站授权；扩展小窗关闭也不会丢失这次页面选择。";
  intent.timer = setTimeout(() => cancelTrackIntent(intent, "网站授权等待已超时。未加入追踪，请重新选择页面。"), TRACK_PERMISSION_TIMEOUT);
  try {
    // Chrome propagates a privileged popup click's user gesture into the
    // service worker's onMessage callback. This call MUST remain synchronous,
    // before any await, so the native prompt and its Promise live in the worker
    // rather than the disposable popup. Do not infer acceptance from onAdded.
    const permission = Promise.resolve(chrome.permissions.request({origins: [originPattern(url)]}));
    return finishTrackRequest(intent, permission);
  } catch (_) {
    cancelTrackIntent(intent, "无法发起网站授权。请点击扩展按钮重新选择页面。");
    return Promise.resolve({ok: false, message: trackingMessage});
  }
}
function save() { return chrome.storage.local.set({sources, pendingForgets}); }
function send(message) {
  if (!port || !connected) return false;
  try { port.postMessage(message); return true; } catch (_) { return false; }
}
function setStatus(source, status, detail = "") {
  if (sources[source.id] !== source) return Promise.resolve();
  source.status = status;
  source.checkedAt = Date.now();
  source.detail = detail;
  send({type: "observation", sourceId: source.id, url: source.url, title: source.title || "已选择的网页",
    status, coverage: "none", tabId: source.tabId, windowId: source.windowId});
  return save();
}

async function connect() {
  await ready;
  if (port || connecting) return;
  connecting = true;
  bridgeReason = "connecting";
  bridgeMessage = "正在连接本机 haochen…";
  try {
    const next = chrome.runtime.connectNative(HOST);
    port = next;
    next.onMessage.addListener(message => {
      if (port !== next) return;
      if (message.type === "ack" && message.operation === "hello") {
        if (typeof message.sessionId !== "string" || !/^[A-Za-z0-9_-]{8,80}$/.test(message.sessionId)) {
          next.disconnect(); port = null; connected = false; connecting = false; sessionId = null;
          clearTimeout(connectionTimer); connectionTimer = null;
          bridgeReason = "protocol_mismatch";
          bridgeMessage = "本机桥接版本不匹配。请在 haochen 重新完成本机连接后重试。";
          return;
        }
        sessionId = message.sessionId;
        connected = true;
        connecting = false;
        clearTimeout(connectionTimer); connectionTimer = null;
        bridgeReason = "connected";
        bridgeMessage = "已连接 haochen · 只在本机传递你选择的网页文字";
        for (const sourceId of pendingForgets) send({type: "forget", sourceId});
        for (const source of Object.values(sources)) refresh(source);
      } else if (message.type === "ack" && message.operation === "forget") {
        pendingForgets = pendingForgets.filter(id => id !== message.sourceId);
        save();
      } else if (message.type === "untrack") {
        // haochen 总览侧忽略动态时同步停止追踪（B-11 完整版）：从扩展自己的追踪
        // 名单移除该来源并持久化，之后不再为它上报观察。
        const sid = typeof message.sourceId === "string" ? message.sourceId : "";
        if (sid && sources[sid]) { delete sources[sid]; save(); }
        if (message.commandId && port === next && connected) {
          send({type: "untrack_result", commandId: message.commandId, status: "done"});
        }
      } else if (message.type === "focus") {
        // Native-only operation. Content scripts and popup runtime messages have
        // no route to this handler. No URL opening or arbitrary script commands.
        focusExisting(message, next).then(status => {
          if (port === next && connected) send({type: "focus_result", commandId: message.commandId, status});
        }).catch(() => {
          if (port === next && connected) send({type: "focus_result", commandId: message.commandId, status: "error"});
        });
      } else if (message.type === "error") {
        bridgeMessage = message.message || "haochen 拒绝了无效的桥接消息";
        connected = false;
        next.disconnect();
      }
    });
    next.onDisconnect.addListener(() => {
      // Consume Chrome's runtime error without logging potentially sensitive values.
      const error = chrome.runtime.lastError?.message || "";
      if (port !== next) return;
      clearTimeout(connectionTimer); connectionTimer = null;
      port = null; connected = false; connecting = false; sessionId = null;
      if (/not found/i.test(error)) {
        bridgeReason = "host_missing"; bridgeMessage = "扩展已安装，但本机桥接还没配置。请回到 haochen 完成本机连接。";
      } else if (/forbidden|access.*denied/i.test(error)) {
        bridgeReason = "origin_mismatch"; bridgeMessage = "桥接绑定的扩展 ID 不匹配。请用下方 ID 在 haochen 重新完成本机连接。";
      } else if (/failed to start|exited/i.test(error)) {
        bridgeReason = "runtime_unavailable"; bridgeMessage = "桥接程序未能启动。若刚升级或移动了应用，请在 haochen 重新完成本机连接。";
      } else {
        bridgeReason = "disconnected";
        bridgeMessage = error ? "连接未成功，请确认 haochen 中的 Chrome 连接已开启，再点击重新连接。" : "haochen 桥接已断开";
      }
    });
    next.postMessage({type: "hello", version: 1, clientId});
    connectionTimer = setTimeout(() => {
      if (port !== next || connected) return;
      next.disconnect(); port = null; connecting = false; sessionId = null;
      bridgeReason = "timeout"; bridgeMessage = "本机连接超过 5 秒未响应，请确认应用可运行并重新连接。";
    }, 5000);
  } catch (_) {
    port = null; connecting = false; connected = false;
    clearTimeout(connectionTimer); connectionTimer = null;
    bridgeReason = "connection_failed"; bridgeMessage = "无法连接 haochen 本机桥接";
  }
}

async function focusExisting(message, nativePort) {
  if (focusBusy) return "busy";
  focusBusy = true;
  try {
    const target = message.target;
    if (!target || !/^[A-Za-z0-9_-]{8,80}$/.test(message.commandId || "") ||
        !Number.isFinite(message.expiresAt) || message.expiresAt > Date.now() + 6000) return "error";
    const source = sources[target.sourceId];
    const check = () => {
      if (Date.now() >= message.expiresAt) return "timeout";
      if (port !== nativePort || !connected || !sessionId || target.sessionId !== sessionId ||
          target.clientId !== clientId) return "disconnected";
      if (!source || sources[target.sourceId] !== source) return "not_watched";
      if (target.kind !== "browser" || target.sourceId !== source.id || target.url !== source.url ||
          target.tabId !== source.tabId || target.windowId !== source.windowId ||
          !Number.isInteger(target.tabId) || !Number.isInteger(target.windowId)) return "target_changed";
      if (["tab_closed", "suspended", "target_changed", "permission_required"].includes(source.status)) return source.status;
      return null;
    };
    const tabFailure = tab => {
      if (tab.incognito || tab.id !== target.tabId || tab.windowId !== target.windowId) return "target_changed";
      if (tab.discarded || tab.frozen) return "suspended";
      try { if (pageURL(tab.url) !== target.url) return "target_changed"; }
      catch (_) { return "target_changed"; }
      return null;
    };
    let failure = check();
    if (failure) return failure;
    if (!await chrome.permissions.contains({origins: [originPattern(source.url)]})) return "permission_required";
    if ((failure = check())) return failure;
    let tab;
    try { tab = await chrome.tabs.get(target.tabId); } catch (_) { return "tab_closed"; }
    if ((failure = check()) || (failure = tabFailure(tab))) return failure;
    // Recheck authorization after asynchronous tab lookup; never request new
    // permission, reload a suspended page or create a replacement tab.
    if (!await chrome.permissions.contains({origins: [originPattern(source.url)]})) return "permission_required";
    if ((failure = check())) return failure;
    try { tab = await chrome.tabs.get(target.tabId); } catch (_) { return "tab_closed"; }
    if ((failure = check()) || (failure = tabFailure(tab))) return failure;
    try { tab = await chrome.tabs.update(target.tabId, {active: true}); }
    catch (_) { return "error"; }
    if ((failure = check()) || (failure = tabFailure(tab))) return failure;
    let window;
    try { window = await chrome.windows.update(target.windowId, {focused: true}); }
    catch (_) { return "error"; }
    if (window.id !== target.windowId || !window.focused) return "error";
    if ((failure = check())) return failure;
    try { tab = await chrome.tabs.get(target.tabId); } catch (_) { return "tab_closed"; }
    if ((failure = check()) || (failure = tabFailure(tab))) return failure;
    if (!tab.active) return "error";
    return "focused";
  } finally {
    focusBusy = false;
  }
}

/* Runs in the page's isolated world, never in MAIN world. No eval/remote code. */
function startObserver(sourceId, expectedURL, maxContent) {
  const key = "__haochenReadOnlyObserverV1";
  globalThis[key]?.stop();
  let stopped = false, timer = null, lastText = null, lastSend = 0;
  const report = () => {
    if (stopped || location.href !== expectedURL) return;
    // Build a clone: do not mutate the page, read form values, or access cookies.
    const root = document.body;
    if (!root) return;
    const clone = root.cloneNode(true);
    clone.querySelectorAll("script,style,noscript,input,textarea,select,form,[contenteditable],[role=textbox],[hidden],[aria-hidden=true]")
      .forEach(node => node.remove());
    const raw = (clone.textContent || "").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
    const content = raw.slice(0, maxContent);
    const title = document.title.slice(0, 500);
    const digestInput = title + "\n" + content;
    const now = Date.now();
    // Heartbeat every 30s still proves an actual check; content change is separate.
    if (digestInput === lastText && now - lastSend < 25000) return;
    lastText = digestInput; lastSend = now;
    chrome.runtime.sendMessage({type: "captured", sourceId, url: expectedURL, title, content,
      coverage: raw.length > maxContent ? "main_frame_text_truncated" : "main_frame_text"})
      .then(response => { if (!response?.ok) stop(); }).catch(() => stop());
  };
  const schedule = () => { if (!timer) timer = setTimeout(() => { timer = null; report(); }, 1200); };
  const observer = new MutationObserver(schedule);
  const pulse = setInterval(report, 30000);
  const stop = () => { stopped = true; clearTimeout(timer); clearInterval(pulse); observer.disconnect(); };
  globalThis[key] = {stop, sourceId};
  observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ["hidden", "aria-hidden", "class", "style"]});
  report();
}

async function refresh(source) {
  // Untracked or changed binding while queued work was waiting: don't revive it.
  if (sources[source.id] !== source) return;
  if (!await chrome.permissions.contains({origins: [originPattern(source.url)]})) {
    await setStatus(source, "permission_required");
    return;
  }
  let tab;
  try { tab = await chrome.tabs.get(source.tabId); }
  catch (_) { await setStatus(source, "tab_closed"); return; }
  if (sources[source.id] !== source) return;
  if (tab.discarded || tab.frozen) { await setStatus(source, "suspended"); return; }
  // tab URL can be unavailable after revocation; never substitute another tab.
  let currentURL = "";
  try { currentURL = pageURL(tab.url); } catch (_) { /* restricted browser URL */ }
  if (currentURL !== source.url) { await setStatus(source, "target_changed"); return; }
  source.windowId = tab.windowId;
  if (tab.status === "loading") { await setStatus(source, "reading"); return; }
  try {
    await chrome.scripting.executeScript({target: {tabId: source.tabId, frameIds: [0]},
      world: "ISOLATED", func: startObserver, args: [source.id, source.url, MAX_CONTENT]});
  } catch (_) { if (sources[source.id] === source) await setStatus(source, "error"); }
}

async function stopObserver(source) {
  try {
    await chrome.scripting.executeScript({target: {tabId: source.tabId, frameIds: [0]}, world: "ISOLATED",
      func: () => globalThis.__haochenReadOnlyObserverV1?.stop()});
  } catch (_) { /* tab may already be closed or authorization revoked */ }
}

async function handle(message, sender) {
  await ready;
  if (message.type === "captured") {
    const source = sources[message.sourceId];
    // Never trust sourceId supplied by a frame without validating its exact binding.
    if (!source || sender.frameId !== 0 || sender.tab?.id !== source.tabId || sender.url !== source.url ||
        message.url !== source.url || typeof message.content !== "string" || message.content.length > MAX_CONTENT ||
        typeof message.title !== "string" || message.title.length > 500) return {ok: false};
    if (!await chrome.permissions.contains({origins: [originPattern(source.url)]}) || sources[source.id] !== source) return {ok: false};
    source.title = message.title; source.status = "available"; source.checkedAt = Date.now();
    source.detail = "只读主框架文字；未包含图片、输入框和跨域框架";
    send({...message, type: "observation", status: "available", tabId: source.tabId, windowId: source.windowId});
    await save();
    return {ok: true};
  }
  // Control commands only come from our popup, never a content script/web page.
  if (sender.url !== chrome.runtime.getURL("popup.html")) return {ok: false};
  if (message.type === "state") return {ok: true, sources: Object.values(sources), connected,
    connecting, bridgeReason, bridgeMessage, extensionId: chrome.runtime.id, trackingMessage,
    pendingTrack: pendingTrack ? {id: pendingTrack.id, tabId: pendingTrack.tabId,
      windowId: pendingTrack.windowId, url: pendingTrack.url, expiresAt: pendingTrack.expiresAt} : null};
  if (message.type === "cancelTrackRequest") {
    if (pendingTrack && message.requestId === pendingTrack.id) {
      cancelTrackIntent(pendingTrack, "已取消这次页面追踪选择；不会在之后授权时自动加入。");
    }
    return {ok: true};
  }
  if (message.type === "reconnect") {
    if (port) port.disconnect(); port = null; connecting = false; connected = false;
    await connect(); return {ok: true};
  }
  const source = sources[message.sourceId];
  if (!source) throw new Error("该页面已不在追踪列表中。");
  if (message.type === "refresh") { await refresh(source); return {ok: true}; }
  if (message.type === "remove") {
    delete sources[source.id];
    await stopObserver(source);
    pendingForgets = [...new Set([...pendingForgets, source.id])].slice(-64);
    send({type: "forget", sourceId: source.id});
    await save(); return {ok: true};
  }
  return {ok: false};
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // A closed popup only loses the response channel, not the permission/track
  // transaction. Request the permission before entering async handle().
  const operation = message?.type === "requestTrack" ? requestTracking(message, sender) : handle(message, sender);
  const reply = result => { try { sendResponse(result); } catch (_) { /* Popup may have been destroyed. */ } };
  operation.then(reply).catch(error => reply({ok: false,
    message: error instanceof Error ? error.message : "网页追踪操作失败"}));
  return true;
});
chrome.tabs.onRemoved.addListener(async tabId => {
  if (pendingTrack?.tabId === tabId) cancelTrackIntent(pendingTrack, "所选标签页已关闭，未加入追踪。");
  await ready;
  for (const source of Object.values(sources)) if (source.tabId === tabId) await setStatus(source, "tab_closed");
});
chrome.tabs.onUpdated.addListener(async (tabId, change) => {
  if (pendingTrack?.tabId === tabId && (change.url || change.status === "loading" || change.discarded || change.frozen)) {
    cancelTrackIntent(pendingTrack, "所选页面在授权期间发生变化，未加入追踪。请重新选择。");
  }
  if (!change.url && !change.status && change.discarded === undefined && change.frozen === undefined) return;
  await ready;
  for (const source of Object.values(sources)) if (source.tabId === tabId) await refresh(source);
});
chrome.permissions.onRemoved.addListener(async () => {
  cancelTrackIntent(pendingTrack, "网站权限已撤回，这次页面选择已取消。");
  await ready;
  for (const source of Object.values(sources)) await refresh(source);
});
chrome.tabs.onReplaced?.addListener((_, removedTabId) => {
  if (pendingTrack?.tabId === removedTabId) cancelTrackIntent(pendingTrack, "所选标签页已被替换，未加入追踪。");
});
chrome.alarms.onAlarm.addListener(async alarm => {
  if (alarm.name !== "haochen-check") return;
  await ready; await connect(); send({type: "ping"});
  for (const source of Object.values(sources)) await refresh(source);
});
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("haochen-check", {periodInMinutes: 0.5}));
// A native port keeps MV3 alive. Alarm recovers after app/browser restarts.
ready.then(() => { chrome.alarms.create("haochen-check", {periodInMinutes: 0.5}); connect(); });
