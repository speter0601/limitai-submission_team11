const API = "http://localhost:8000";
const DASHBOARD = `${API}/?session=`;
const naverUrl = (q) => `https://search.naver.com/search.naver?query=${encodeURIComponent(q)}`;

let queue = Promise.resolve();
async function readTabs() {
  return (await chrome.storage.session.get("tabs")).tabs || {};
}
function updateTabs(fn) {
  queue = queue.then(async () => {
    const tabs = await readTabs();
    fn(tabs);
    await chrome.storage.session.set({ tabs });
  });
  return queue;
}

async function callApi(path, body, method) {
  const res = await fetch(`${API}${path}`, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `서버 오류 ${res.status}`);
  return data;
}

async function handle(msg, sender) {
  switch (msg.type) {
    case "focus-api":
      return callApi(msg.path, msg.body, msg.method);
    case "get-session": {
      await queue;
      return (await readTabs())[sender.tab?.id] || null;
    }
    case "open-search": {
      const tabId = msg.sameTab && sender.tab ? sender.tab.id : (await chrome.tabs.create({ url: "about:blank" })).id;
      await updateTabs((tabs) => (tabs[tabId] = msg.session_id));
      await chrome.tabs.update(tabId, { url: naverUrl(msg.query) });
      return { tabId };
    }
    case "open-dashboard":
      await chrome.tabs.create({ url: DASHBOARD + msg.session_id });
      return true;
    case "end-session":
      await updateTabs((tabs) => {
        for (const id of Object.keys(tabs)) if (tabs[id] === msg.session_id) delete tabs[id];
      });
      return true;
    default:
      throw new Error(`알 수 없는 요청: ${msg.type}`);
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handle(msg, sender).then(
    (data) => sendResponse({ ok: true, data }),
    (err) => sendResponse({ ok: false, error: err.message })
  );
  return true;
});

chrome.tabs.onCreated.addListener((tab) => {
  if (tab.openerTabId == null) return;
  updateTabs((tabs) => {
    if (tabs[tab.openerTabId]) tabs[tab.id] = tabs[tab.openerTabId];
  });
});

chrome.tabs.onRemoved.addListener((tabId) => updateTabs((tabs) => delete tabs[tabId]));
