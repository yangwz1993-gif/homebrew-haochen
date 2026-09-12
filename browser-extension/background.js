/* Only explicit page selections enter sources. Never enumerate all browser tabs. */
const HOST = "com.haochen.browser";
const MAX_SOURCES = 32;
const MAX_CONTENT = 60000;
let sources = {}, clientId, port = null, connecting = false, connected = false;
let bridgeMessage = "尚未连接 haochen。请先在应用中安装桥接。";
let bridgeReason = "not_connected", connectionTimer = null;
let pendingForgets = [];
let sessionId = null, focusBusy = false;
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
    connecting, bridgeReason, bridgeMessage, extensionId: chrome.runtime.id};
  if (message.type === "reconnect") {
    if (port) port.disconnect(); port = null; connecting = false; connected = false;
    await connect(); return {ok: true};
  }
  if (message.type === "track") {
    const tab = await chrome.tabs.get(message.tabId);
    const url = pageURL(tab.url);
    if (url !== message.url) throw new Error("授权期间页面已变化，请重新选择当前页面。");
    if (tab.incognito) throw new Error("不在无痕窗口内追踪网页。");
    if (!await chrome.permissions.contains({origins: [originPattern(url)]})) throw new Error("尚未获得该网站权限。");
    const previous = Object.values(sources).find(source => source.url === url);
    if (!previous && Object.keys(sources).length >= MAX_SOURCES) throw new Error("最多追踪 32 个页面，请先移除不再需要的来源。");
    if (previous) await stopObserver(previous);
    const source = {id: previous?.id || crypto.randomUUID(), url, title: (tab.title || url).slice(0, 500),
      tabId: tab.id, windowId: tab.windowId, status: "reading", checkedAt: Date.now()};
    // One binding per tab. Navigating to a new URL is a new explicitly selected source.
    for (const old of Object.values(sources)) {
      if (old.tabId === source.tabId && old.id !== source.id) await setStatus(old, "target_changed");
    }
    sources[source.id] = source;
    await save(); await connect(); await refresh(source); return {ok: true};
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
  handle(message, sender).then(sendResponse).catch(error => sendResponse({ok: false,
    message: error instanceof Error ? error.message : "网页追踪操作失败"}));
  return true;
});
chrome.tabs.onRemoved.addListener(async tabId => {
  await ready;
  for (const source of Object.values(sources)) if (source.tabId === tabId) await setStatus(source, "tab_closed");
});
chrome.tabs.onUpdated.addListener(async (tabId, change) => {
  if (!change.url && !change.status && change.discarded === undefined && change.frozen === undefined) return;
  await ready;
  for (const source of Object.values(sources)) if (source.tabId === tabId) await refresh(source);
});
chrome.permissions.onRemoved.addListener(async () => {
  await ready;
  for (const source of Object.values(sources)) await refresh(source);
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
