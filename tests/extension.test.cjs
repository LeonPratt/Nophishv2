const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../extension/background.js"), "utf8");

function harness() {
  const state = {
    tab: { id: 1, windowId: 7, active: true, url: "http://127.0.0.1:8001/login.html" },
    now: 10_000, posts: [], captures: 0, badges: [], logs: [], messages: [], colors: [],
  };
  const timers = new Map();
  let timerId = 0;
  const setTimeout = (fn, delay) => { timers.set(++timerId, { fn, due: state.now + delay }); return timerId; };
  const clearTimeout = (id) => timers.delete(id);
  state.advance = async (ms) => {
    const target = state.now + ms;
    let iterations = 0;
    while (true) {
      const next = [...timers.entries()].sort((a, b) => a[1].due - b[1].due)[0];
      if (!next || next[1].due > target) break;
      assert.ok(++iterations < 100, "Timer loop should remain bounded");
      timers.delete(next[0]);
      state.now = next[1].due;
      await next[1].fn();
    }
    state.now = target;
  };
  const chrome = {
    runtime: { getURL: (file) => "chrome-extension://test/" + file,
      onMessage: { addListener(fn) { state.message = fn; } } },
    tabs: {
      async get(tabId) { return { ...state.tab, active: tabId === 1 && state.tab.active }; },
      async captureVisibleTab(windowId, options) {
        state.captures++;
        assert.equal(windowId, 7);
        assert.equal(options.format, "png");
        if (state.permissionError) throw new Error("Permission denied");
        if (state.onCapture) await state.onCapture();
        return "data:image/png;base64,SCREENSHOT";
      },
      onRemoved: { addListener() {} },
      onUpdated: { addListener(fn) { state.updated = fn; } },
      onActivated: { addListener(fn) { state.activated = fn; } },
      async sendMessage(tabId, message) { state.messages.push(message); },
    },
    action: {
      async setBadgeText({ text }) { state.badges.push(text); },
      async setBadgeBackgroundColor({ color }) { state.colors.push(color); },
      async setTitle() {},
      onClicked: { addListener(fn) { state.click = fn; } },
    },
    webNavigation: {
      onCommitted: { addListener(fn) { state.commit = fn; } },
      onHistoryStateUpdated: { addListener(fn) { state.history = fn; } },
      onReferenceFragmentUpdated: { addListener(fn) { state.fragment = fn; } },
    },
  };
  const context = vm.createContext({
    chrome,
    URL: require("node:url").URL,
    Date: { now: () => state.now },
    AbortSignal,
    setTimeout, clearTimeout,
    console: { info(...args) { state.logs.push(args); } },
    async fetch(url, options) {
      state.posts.push({ url, options, body: options.body ? JSON.parse(options.body) : null });
      if (state.offline) throw new Error("Connection refused");
      if (url.endsWith("/domain-policies")) return { ok: true, async json() {
        if (options.method === "GET") return { entries: [] };
        const policy = JSON.parse(options.body).policy;
        if (options.method === "DELETE") return { hostname: "127.0.0.1", policy: null };
        return { hostname: "127.0.0.1", policy, source: "user_list", brand: null,
          verdict: policy === "whitelist" ? "safe" : "dangerous", reason: "Saved domain decision" };
      } };
      if (state.fetchWait) await state.fetchWait;
      return { ok: true, async json() {
        return state.result || {
          verdict: "dangerous", brand: "microsoft", reason: "Possible microsoft impersonation",
        };
      } };
    },
  });
  vm.runInContext(source, context);
  state.direct = () => vm.runInContext("inspect(1)", context);
  state.signal = (documentId = "document-1") => {
    state.message({ type: "inspect" }, { tab: { id: 1 }, frameId: 0, documentId }, () => {});
  };
  state.inspect = async (documentId) => { state.signal(documentId); await state.advance(700); };
  state.policy = (message, sender = { tab: { id: 1 }, frameId: 0, documentId: "document-1", url: state.tab.url }) =>
    new Promise((resolve) => state.message(message, sender, resolve));
  state.click = () => state.policy({ type: "inspectActive", tabId: 1, url: state.tab.url },
    { url: "chrome-extension://test/popup.html" });
  return state;
}

test("sends correct screenshot and URL, accepts a classified brand, deduplicates", async () => {
  const h = harness();
  await h.inspect();
  assert.equal(h.posts.length, 1);
  assert.equal(h.posts[0].url, "http://127.0.0.1:8000/analyze");
  assert.equal(h.posts[0].body.url, h.tab.url);
  assert.equal(h.posts[0].body.screenshot, "data:image/png;base64,SCREENSHOT");
  assert.equal(h.badges.at(-1), "!");
  assert.equal(h.colors.at(-1), "#b91c1c");
  assert.equal(h.messages.at(-1).result.verdict, "dangerous");
  assert.ok(!JSON.stringify(h.logs).includes(h.tab.url));
  assert.ok(!JSON.stringify(h.logs).includes("SCREENSHOT"));
  h.now += 2000;
  await h.inspect();
  assert.equal(h.posts.length, 1);
  h.tab.url += "#next";
  await h.inspect();
  assert.equal(h.posts.length, 2);
});

test("inactive and restricted tabs never capture", async () => {
  const h = harness();
  h.tab.active = false;
  await h.inspect();
  h.tab.active = true;
  h.tab.url = "chrome://extensions";
  await h.inspect();
  assert.equal(h.captures, 0);
});

test("tab switch or navigation during capture discards the screenshot", async () => {
  for (const change of [h => { h.tab.active = false; }, h => { h.tab.url += "/next"; }]) {
    const h = harness();
    h.onCapture = () => change(h);
    await h.inspect();
    assert.equal(h.posts.length, 0);
  }
});

test("offline requests show OFF and do not spam automatic retries", async () => {
  const h = harness();
  h.offline = true;
  await h.inspect();
  assert.equal(h.badges.at(-1), "OFF");
  h.now += 2000;
  h.tab.url += "/next";
  await h.inspect();
  assert.equal(h.posts.length, 1);
  h.now += 15_000;
  h.offline = false;
  await h.inspect();
  assert.equal(h.badges.at(-1), "!");
});

test("capture permission errors and invalid responses show ERR", async () => {
  const h = harness();
  h.permissionError = true;
  await h.inspect();
  assert.equal(h.badges.at(-1), "ERR");
  assert.equal(h.posts.length, 0);
  h.permissionError = false;
  h.result = { verdict: "safe", brand: "unknown", reason: "unexpected" };
  h.now += 2000;
  await h.inspect("document-2");
  assert.equal(h.badges.at(-1), "ERR");
});

test("not_login clears the badge; unknown is visibly inconclusive", async () => {
  for (const [verdict, brand, badge] of [["not_login", null, ""], ["unknown", null, "?"], ["safe", "google", ""]]) {
    const h = harness();
    h.result = { verdict, brand, reason: "Analysis result" };
    await h.inspect();
    assert.equal(h.badges.at(-1), badge);
  }
});

test("event bursts coalesce and manual retry bypasses deduplication", async () => {
  const h = harness();
  for (let i = 0; i < 10; i++) h.signal();
  await h.advance(700);
  assert.equal(h.posts.length, 1);
  h.now += 2000;
  await h.click();
  await h.advance(1);
  assert.equal(h.posts.length, 2);
});

test("same-URL history state updates do not repeat unchanged captures", async () => {
  const h = harness();
  await h.inspect();
  h.now += 2000;
  for (let i = 0; i < 5; i++) h.history({ tabId: 1, frameId: 0, documentId: "document-1", url: h.tab.url });
  await h.advance(700);
  assert.equal(h.posts.length, 1);
});

test("SPA navigation invalidates an in-flight result, including a return to the same URL", async () => {
  const h = harness();
  let release;
  h.fetchWait = new Promise(resolve => { release = resolve; });
  const running = h.direct();
  await new Promise(setImmediate);
  assert.equal(h.posts.length, 1);
  const original = h.tab.url;
  h.tab.url += "/next";
  h.history({ tabId: 1, frameId: 0, documentId: "document-1" });
  h.tab.url = original;
  h.history({ tabId: 1, frameId: 0, documentId: "document-1" });
  await h.advance(700); // Busy: keep the latest timer instead of dropping it.
  release();
  await running;
  assert.equal(h.messages.filter(m => m.type === "analysis").length, 0);
  await h.advance(1000);
  assert.equal(h.posts.length, 2);
  assert.equal(h.messages.filter(m => m.type === "analysis").length, 1);
});

test("navigation without a DOM mutation is inspected and old UI is cleared", async () => {
  const h = harness();
  await h.inspect();
  h.now += 2000;
  h.tab.url += "#route";
  h.fragment({ tabId: 1, frameId: 0, documentId: "document-1" });
  await h.advance(700);
  assert.equal(h.posts.length, 2);
  assert.equal(h.posts[1].body.url, h.tab.url);
  assert.ok(h.messages.some(m => m.type === "clearNotice"));
});

test("switching away and back during capture never posts a possibly mismatched image", async () => {
  const h = harness();
  h.onCapture = () => {
    h.activated({ tabId: 2 });
    h.activated({ tabId: 1 });
    h.onCapture = null;
  };
  await h.inspect();
  assert.equal(h.posts.length, 0);
  await h.advance(1500);
  assert.equal(h.posts.length, 1);
});

// User decisions do not require a detected brand.
test("whitelist and blacklist verdicts accept a null brand only with a matching list source", async () => {
  for (const [policy, verdict, badge] of [["whitelist", "safe", ""], ["blacklist", "dangerous", "!"]]) {
    const h = harness();
    h.result = { policy, verdict, source: "user_list", brand: null, reason: "User decision" };
    await h.inspect();
    assert.equal(h.badges.at(-1), badge);
  }
  const h = harness();
  h.result = { policy: "whitelist", verdict: "dangerous", source: "user_list", brand: null, reason: "Invalid" };
  await h.inspect();
  assert.equal(h.badges.at(-1), "ERR");
});

test("warning decision uses the sender's current domain and clears the warning immediately", async () => {
  const h = harness();
  await h.inspect();
  const response = await h.policy({ type: "setDomainPolicy", url: h.tab.url, domain: "attacker.invalid", policy: "whitelist" });
  assert.equal(response.ok, true);
  assert.equal(h.posts.at(-1).body.domain, h.tab.url);
  assert.equal(h.posts.at(-1).options.headers["X-NoPhish-Action"], "domain-policy");
  assert.equal(h.badges.at(-1), "");
  assert.equal(h.messages.at(-1).result.verdict, "safe");
});

test("stale or untrusted pages cannot mutate arbitrary domain decisions", async () => {
  const h = harness();
  await h.inspect();
  const before = h.posts.length;
  assert.equal((await h.policy({ type: "setDomainPolicy", url: "https://other.invalid/", policy: "whitelist" })).ok, false);
  assert.equal((await h.policy({ type: "setDomainPolicy", url: h.tab.url, policy: "whitelist" },
    { tab: { id: 1 }, frameId: 1, url: h.tab.url })).ok, false);
  assert.equal((await h.policy({ type: "domainPolicies", method: "PUT", domain: "other.invalid", policy: "whitelist" })).ok, false);
  assert.equal(h.posts.length, before);
});

test("extension options can list, replace and remove entries", async () => {
  const h = harness();
  const sender = { url: "chrome-extension://test/options.html" };
  assert.equal((await h.policy({ type: "domainPolicies", method: "GET" }, sender)).ok, true);
  assert.equal((await h.policy({ type: "domainPolicies", method: "PUT", domain: "example.com", policy: "blacklist" }, sender)).ok, true);
  assert.equal((await h.policy({ type: "domainPolicies", method: "DELETE", domain: "example.com" }, sender)).ok, true);
  assert.equal(h.posts[1].body.domain, "example.com");
  assert.equal(h.posts[2].options.method, "DELETE");
});

test("popup can manage lists and request inspection, while stale tabs are rejected", async () => {
  const h = harness();
  const sender = { url: "chrome-extension://test/popup.html" };
  assert.equal((await h.policy({ type: "domainPolicies", method: "GET" }, sender)).ok, true);
  assert.equal((await h.policy({ type: "domainPolicies", method: "PUT", domain: "example.com", policy: "whitelist" }, sender)).ok, true);
  assert.equal((await h.policy({ type: "inspectActive", tabId: 1, url: "https://stale.invalid/" }, sender)).ok, false);
  const count = h.posts.length;
  assert.equal((await h.click()).ok, true);
  await h.advance(1);
  assert.equal(h.posts.length, count + 1);
  assert.equal(h.captures, 1);
});

test("a new domain decision invalidates an older in-flight model result", async () => {
  const h = harness();
  let release;
  h.fetchWait = new Promise(resolve => { release = resolve; });
  const pending = h.direct();
  for (let i = 0; i < 20; i++) await Promise.resolve();
  h.fetchWait = null;
  const saved = await h.policy({ type: "setDomainPolicy", url: h.tab.url, policy: "whitelist" },
    { tab: { id: 1 }, frameId: 0, url: h.tab.url });
  assert.equal(saved.ok, true);
  release();
  await pending;
  assert.equal(h.badges.at(-1), "");
  assert.equal(h.messages.at(-1).result.verdict, "safe");
});
