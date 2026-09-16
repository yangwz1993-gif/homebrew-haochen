const $ = id => document.getElementById(id);
let active = null;
let rendering = false, lastSources = null, connected = false, tracking = false;
let pendingRequestId = null, lastTrackingMessage = "";
const labels = {available: "已检查", reading: "读取中", tab_closed: "标签页已关闭", suspended: "页面休眠",
  target_changed: "页面已切换 · 未读取新网址", permission_required: "需重新授权", error: "采集失败"};
function notify(message) { $("notice").textContent = message; }
async function command(message) {
  const result = await chrome.runtime.sendMessage(message);
  if (!result?.ok) throw new Error(result?.message || "连接暂不可用，请重试");
  return result;
}
async function render() {
  if (rendering) return;
  rendering = true;
  try {
  const state = await command({type: "state"});
  connected = state.connected;
  $("bridge-status").textContent = state.bridgeMessage;
  const actual = state.sources.filter(source => source.status === "available");
  $("connection-step").textContent = !state.connected ? "第 2 步 / 4 · 扩展已运行，等待本机桥接" :
    actual.length ? `第 4 步 / 4 · 已读取 ${actual.length} 个页面` : "第 3 步 / 4 · 连接成功，选择网页并授权";
  $("reconnect").disabled = state.connecting;
  pendingRequestId = state.pendingTrack?.id || null;
  $("track").disabled = !active || !connected || tracking || Boolean(pendingRequestId);
  $("track").textContent = pendingRequestId ? "正在等待网站授权…" : "授权此网站并追踪当前页";
  $("cancel-track").hidden = !pendingRequestId;
  if (state.trackingMessage && state.trackingMessage !== lastTrackingMessage) {
    lastTrackingMessage = state.trackingMessage; notify(state.trackingMessage);
  }
  if (!state.connected && !state.connecting) $("first-connection").open = true;
  $("extension-id").textContent = state.extensionId;
  $("count").textContent = String(state.sources.length);
  const signature = JSON.stringify([state.connected,state.sources]);
  if (signature === lastSources || $("sources").contains(document.activeElement)) return;
  lastSources = signature;
  $("sources").replaceChildren();
  if (!state.sources.length) {
    const empty = document.createElement("p"); empty.className = "hint";
    empty.textContent = "还没有追踪页面。打开想关注的网页，再点击上方按钮。"; $("sources").append(empty);
  }
  for (const source of state.sources) {
    const card = document.createElement("article"); card.className = "source";
    const title = document.createElement("strong"); title.textContent = source.title || "已选择的网页";
    const url = document.createElement("a"); url.href = source.url; url.target = "_blank";
    url.rel = "noopener noreferrer"; url.textContent = source.url;
    const status = document.createElement("small");
    status.textContent = (state.connected ? (labels[source.status] || "等待检查") : "桥接离线") +
      (source.checkedAt ? " · " + new Date(source.checkedAt).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "");
    const actions = document.createElement("div"); actions.className = "source-actions";
    for (const [type, text] of [["refresh", "重新检查"], ["remove", "移除追踪"]]) {
      const button = document.createElement("button"); button.className = "subtle"; button.textContent = text;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try { await command({type, sourceId: source.id}); await render(); }
        catch (error) { notify(error.message); button.disabled = false; }
      }); actions.append(button);
    }
    card.append(title, url, status, actions); $("sources").append(card);
  }
  } finally { rendering = false; }
}
$("reconnect").addEventListener("click", async () => {
  try { await command({type: "reconnect"}); await render(); } catch (error) { notify(error.message); }
});
$("copy-id").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(chrome.runtime.id); notify("扩展 ID 已复制。"); }
  catch (_) { notify("请手动选择并复制上方扩展 ID。"); }
});
// 天梯日报一键连接：只在点击这一刻、只对 cowork 域名请求 cookies 权限，
// 只读 web_session 一个 cookie，经本机桥接交给 haochen（不落盘到扩展存储）。
$("tianti-connect").addEventListener("click", async () => {
  const button = $("tianti-connect"), status = $("tianti-status");
  button.disabled = true;
  status.textContent = "正在请求 cowork 域名的读取授权…";
  try {
    const granted = await chrome.permissions.request({
      permissions: ["cookies"], origins: ["https://cowork.xiaohongshu.com/*"]});
    if (!granted) { status.textContent = "未授权，未连接。授权只在点击时请求、仅用于这一次连接。"; return; }
    const cookie = await chrome.cookies.get({url: "https://cowork.xiaohongshu.com", name: "web_session"});
    if (!cookie?.value) {
      status.textContent = "没有读到 cowork 登录态：请先在 Chrome 里登录 cowork，再点这里。";
      return;
    }
    await command({type: "tiantiCookie", value: cookie.value});
    status.textContent = "已连接天梯日报 ✅ haochen 会每天自动读取你的日报。";
  } catch (error) {
    status.textContent = error.message || "连接失败，请重试。";
  } finally {
    button.disabled = false;
  }
});
$("track").addEventListener("click", async () => {
  if (!active || !connected) return;
  tracking = true;
  $("track").disabled = true;
  try {
    // Send during the click, before any await: Chrome carries this user gesture
    // to the worker. The worker owns both native permission and exact binding;
    // closing this popup cannot strand a granted site with zero tracked pages.
    await command({type: "requestTrack", tabId: active.id, windowId: active.windowId, url: active.url});
    notify("已加入追踪。只有该固定页面会被读取；切换网址不会自动改读。"); await render();
  } catch (error) { notify(error.message); }
  finally { tracking = false; render().catch(() => {}); }
});
$("cancel-track").addEventListener("click", async () => {
  if (!pendingRequestId) return;
  try { await command({type: "cancelTrackRequest", requestId: pendingRequestId}); await render(); }
  catch (error) { notify(error.message); }
});
(async () => {
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.url || !["http:", "https:"].includes(new URL(tab.url).protocol) || tab.incognito ||
        new URL(tab.url).username || new URL(tab.url).password) {
      $("page-title").textContent = "请打开普通 HTTP(S) 网页；不支持设置页或无痕页。";
    } else { active = tab; $("page-title").textContent = tab.title || tab.url; }
    await render();
  } catch (error) { notify(error.message); }
})();
// Native handshake and initial DOM capture complete asynchronously. Keep the
// open popup current without requiring users to close/reopen it to see success.
const poll = setInterval(() => { if (!document.hidden) render().catch(() => {}); }, 1000);
addEventListener("pagehide", () => clearInterval(poll), {once:true});
