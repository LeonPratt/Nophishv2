const ENDPOINT = "http://127.0.0.1:8000/analyze";
const POLICY_ENDPOINT = "http://127.0.0.1:8000/domain-policies";
const tabs = new Map();
let busy = false;
let nextCapture = 0;
let offlineUntil = 0;
let activationVersion = 0;

function stateFor(tabId) {
  if (!tabs.has(tabId)) tabs.set(tabId, { generation: 0, documentId: null, url: null, seen: null, timer: null, login: false, force: false });
  return tabs.get(tabId);
}

async function indicate(tabId, text, title, color = "#666666") {
  await Promise.all([
    chrome.action.setBadgeText({ tabId, text }),
    chrome.action.setBadgeBackgroundColor({ tabId, color }),
    chrome.action.setTitle({ tabId, title }),
  ]).catch(() => {});
}

async function tell(tabId, state, message) {
  const target = state.documentId ? { documentId: state.documentId } : { frameId: 0 };
  return chrome.tabs.sendMessage(tabId, message, target).catch(() => {});
}

// One replaceable debounce timer per tab, not a backlog of captures.
function schedule(tabId, delay = 600, force = false) {
  const state = stateFor(tabId);
  state.force ||= force;
  clearTimeout(state.timer);
  state.timer = setTimeout(async () => {
    state.timer = null;
    const wait = Math.max(nextCapture, state.force ? 0 : offlineUntil) - Date.now();
    if (busy || wait > 0) {
      schedule(tabId, Math.max(busy ? 400 : 0, wait));
      return;
    }
    await inspect(tabId);
  }, delay);
}

function invalidate(tabId, documentId, url = null) {
  const state = stateFor(tabId);
  state.generation++;
  state.documentId = documentId || state.documentId;
  state.url = url;
  state.seen = null;
  state.login = false;
  state.force = false;
  clearTimeout(state.timer);
  state.timer = null;
  void indicate(tabId, "", "NoPhish: waiting to inspect this page");
  void tell(tabId, state, { type: "clearNotice" });
}

async function inspect(tabId) {
  const state = stateFor(tabId);
  if (busy || Date.now() < nextCapture || (!state.force && Date.now() < offlineUntil)) {
    schedule(tabId);
    return;
  }
  const force = state.force;
  state.force = false;
  const generation = state.generation;
  const current = () => tabs.get(tabId) === state && state.generation === generation;
  busy = true;
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!tab.active || !/^https?:\/\//i.test(tab.url || "") || !current()) return;
    state.url = tab.url;
    const key = `${generation}:${tab.url}:${state.login}`;
    if (!force && state.seen === key) return;
    state.seen = key;
    nextCapture = Date.now() + 1100;
    await tell(tabId, state, { type: "clearNotice" }); // Keep our warning out of screenshots.
    await indicate(tabId, "...", "NoPhish: running local analysis");
    const activeAtCapture = activationVersion;
    const screenshot = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    const captured = await chrome.tabs.get(tabId);
    if (!current() || !captured.active || captured.url !== tab.url || activationVersion !== activeAtCapture) {
      if (current()) state.seen = null;
      return;
    }

    let response;
    try {
      response = await fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: tab.url, screenshot }),
        signal: AbortSignal.timeout(10_000),
        redirect: "error",
      });
    } catch {
      offlineUntil = Date.now() + 15_000;
      if (current()) await indicate(tabId, "OFF", "NoPhish: local server unavailable; open popup to retry");
      return;
    }
    if (!response.ok) throw new Error("Local server rejected request");
    const result = await response.json();
    const knownBrand = ["microsoft", "google", "meta"].includes(result?.brand);
    const listed = result?.source === "user_list" && result.brand === null &&
      ((result.policy === "whitelist" && result.verdict === "safe") ||
       (result.policy === "blacklist" && result.verdict === "dangerous"));
    if (!result || !["safe", "dangerous", "unknown", "not_login"].includes(result.verdict) ||
        ![null, "unknown", "microsoft", "google", "meta"].includes(result.brand) ||
        (["safe", "dangerous"].includes(result.verdict) && !knownBrand && !listed) ||
        (result.verdict === "not_login" && result.brand !== null) || typeof result.reason !== "string") {
      throw new Error("Unexpected analysis response");
    }
    const latest = await chrome.tabs.get(tabId);
    if (!current() || latest.url !== tab.url) return;
    const badge = { dangerous: "!", unknown: "?", safe: "", not_login: "" }[result.verdict];
    await indicate(tabId, badge, `NoPhish: ${result.reason}`, result.verdict === "dangerous" ? "#b91c1c" : "#666666");
    await tell(tabId, state, { type: "analysis", url: tab.url, result });
    console.info("NoPhish verdict:", result.verdict, "brand:", result.brand);
  } catch {
    if (current()) await indicate(tabId, "ERR", "NoPhish: capture, permission, or response error; open popup to retry");
  } finally {
    busy = false;
  }
}

function hostname(url) {
  return new URL(url).hostname.toLowerCase().replace(/\.$/, "").replace(/^\[|\]$/g, "");
}

async function domainPolicies(message, sender) {
  const optionsPage = ["options.html", "popup.html"].some(page => sender.url === chrome.runtime.getURL(page));
  const decision = message.type === "setDomainPolicy";
  let domain = message.domain;
  if (decision) {
    if (!sender.tab || sender.frameId !== 0) throw new Error("Invalid page action");
    const tab = await chrome.tabs.get(sender.tab.id);
    const state = stateFor(tab.id);
    if (!tab.active || !/^https?:\/\//i.test(tab.url || "") || message.url !== tab.url ||
        sender.url !== tab.url || (state.documentId && state.documentId !== sender.documentId)) {
      throw new Error("The page changed; retry on the current page");
    }
    domain = tab.url; // Never accept an arbitrary hostname from a content script.
  } else if (!optionsPage) {
    throw new Error("Open the extension popup or options to manage domains");
  }
  const method = decision ? "PUT" : message.method;
  if (!["GET", "PUT", "DELETE"].includes(method) ||
      (method === "PUT" && !["whitelist", "blacklist"].includes(message.policy))) {
    throw new Error("Invalid domain decision");
  }
  const response = await fetch(POLICY_ENDPOINT, {
    method,
    headers: { "Content-Type": "application/json", "X-NoPhish-Action": "domain-policy" },
    ...(method === "GET" ? {} : { body: JSON.stringify({ domain, policy: message.policy }) }),
    signal: AbortSignal.timeout(5000), redirect: "error",
  });
  if (!response.ok) throw new Error("Could not update domain lists; check the server and domain");
  const result = await response.json();
  if (method !== "GET") {
    // Invalidate older model results before applying the user's new decision.
    for (const [tabId, state] of tabs) {
      let tab;
      try { tab = await chrome.tabs.get(tabId); } catch { continue; }
      if (!/^https?:\/\//i.test(tab.url || "") || hostname(tab.url) !== result.hostname) continue;
      invalidate(tabId, state.documentId, tab.url);
      if (method === "DELETE") {
        schedule(tabId, 0, true);
      } else {
        await indicate(tabId, result.verdict === "dangerous" ? "!" : "", `NoPhish: ${result.reason}`,
          result.verdict === "dangerous" ? "#b91c1c" : "#666666");
        await tell(tabId, state, { type: "analysis", url: tab.url, result });
      }
    }
  }
  return { ok: true, ...result };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "inspectActive") {
    if (sender.url !== chrome.runtime.getURL("popup.html")) return;
    chrome.tabs.get(message.tabId).then(tab => {
      if (!tab.active || tab.url !== message.url || !/^https?:\/\//i.test(tab.url || "")) {
        sendResponse({ ok: false, error: "The page changed. Reopen the popup and try again." });
        return;
      }
      schedule(tab.id, 0, true);
      sendResponse({ ok: true });
    }).catch(() => sendResponse({ ok: false, error: "This tab is no longer available." }));
    return true;
  }
  if (["setDomainPolicy", "domainPolicies"].includes(message?.type)) {
    domainPolicies(message, sender).then(sendResponse).catch(() =>
      sendResponse({ ok: false, error: "Could not save domain decision. Check the current page, domain and local server." }));
    return true;
  }
  if (message?.type !== "inspect" || sender.frameId !== 0 || !sender.tab) return;
  const state = stateFor(sender.tab.id);
  if (state.documentId && sender.documentId && state.documentId !== sender.documentId) {
    invalidate(sender.tab.id, sender.documentId);
  }
  state.documentId = sender.documentId || state.documentId;
  state.login ||= Boolean(message.login);
  schedule(sender.tab.id);
  sendResponse({ scheduled: true });
});
chrome.tabs.onRemoved.addListener((tabId) => {
  clearTimeout(tabs.get(tabId)?.timer);
  tabs.delete(tabId);
});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === "complete") schedule(tabId);
});
chrome.tabs.onActivated.addListener(({ tabId }) => {
  activationVersion++;
  schedule(tabId);
});
chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId === 0) invalidate(details.tabId, details.documentId, details.url);
});
for (const event of [chrome.webNavigation.onHistoryStateUpdated, chrome.webNavigation.onReferenceFragmentUpdated]) {
  event.addListener((details) => {
    if (details.frameId !== 0) return;
    if (details.url && details.url === stateFor(details.tabId).url) return;
    invalidate(details.tabId, details.documentId, details.url);
    schedule(details.tabId);
  });
}
