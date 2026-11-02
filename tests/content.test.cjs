const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../extension/content.js"), "utf8");

function harness() {
  const h = { now: 0, posts: [], passwordVisible: false, events: {} };
  const timers = new Map();
  let id = 0;
  function element(tag) {
    const item = { tag, style: {}, children: [], attributes: {}, events: {},
      append(...children) { this.children.push(...children); },
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(name, fn) { this.events[name] = fn; },
      attachShadow() { this.shadow = element("shadow"); return this.shadow; },
      remove() { h.root.children = h.root.children.filter(child => child !== this); },
    };
    return item;
  }
  h.root = element("html");
  const password = { getClientRects: () => h.passwordVisible ? [{}] : [],
    get value() { throw new Error("Input values must never be read"); } };
  h.location = { href: "https://fixture.invalid/" };
  h.document = {
    visibilityState: "visible", documentElement: h.root,
    createElement: element, querySelectorAll: () => [password],
    addEventListener(name, fn) { h.events[name] = fn; },
  };
  vm.runInNewContext(source, {
    URL: require("node:url").URL,
    location: h.location, document: h.document,
    getComputedStyle: () => ({ visibility: "visible" }),
    addEventListener(name, fn) { h.events[name] = fn; },
    setTimeout(fn, delay) { timers.set(++id, { fn, due: h.now + delay }); return id; },
    chrome: { runtime: {
      async sendMessage(message) { h.posts.push(message); return h.response; },
      onMessage: { addListener(fn) { h.message = fn; } },
    } },
    MutationObserver: class { constructor(fn) { h.mutate = fn; } observe() {} },
  });
  h.advance = async (ms = 500) => {
    h.now += ms;
    for (const [key, task] of [...timers]) {
      if (task.due <= h.now) { timers.delete(key); await task.fn(); }
    }
  };
  return h;
}

test("ordinary load captures once; later visible login fields cause one reinspection", async () => {
  const h = harness();
  await h.advance();
  assert.equal(h.posts.length, 1);
  for (let i = 0; i < 20; i++) h.mutate();
  await h.advance();
  assert.equal(h.posts.length, 1);
  h.passwordVisible = true;
  h.mutate();
  await h.advance();
  assert.equal(h.posts.length, 2);
  assert.equal(h.posts[1].login, true);
  h.mutate();
  await h.advance();
  assert.equal(h.posts.length, 2);
});

test("dangerous warning uses an alert, literal text, and a working dismiss button", async () => {
  const h = harness();
  const text = '<img src=x onerror="alert(1)">';
  h.message({ type: "analysis", url: h.location.href, result: { verdict: "dangerous", reason: text } });
  const panel = h.root.children[0].shadow.children[1];
  assert.equal(panel.attributes.role, "alert");
  assert.equal(panel.children[1].textContent, text);
  assert.equal(panel.children[1].innerHTML, undefined);
  panel.children[3].events.click();
  assert.equal(h.root.children.length, 0);
});

test("non-dangerous results and navigation remove warnings; stale URLs are ignored", async () => {
  const h = harness();
  for (const verdict of ["safe", "unknown", "not_login"]) {
    h.message({ type: "analysis", url: h.location.href, result: { verdict: "dangerous", reason: "Warning" } });
    h.message({ type: "analysis", url: h.location.href, result: { verdict, reason: "Result" } });
    assert.equal(h.root.children.length, 0);
  }
  h.message({ type: "analysis", url: "https://stale.invalid/", result: { verdict: "dangerous", reason: "Old" } });
  assert.equal(h.root.children.length, 0);
  h.message({ type: "analysis", url: h.location.href, result: { verdict: "dangerous", reason: "Warning" } });
  h.location.href += "/route";
  h.events.popstate();
  await h.advance();
  assert.equal(h.root.children.length, 0);
});

test("warning saves only trusted current-page whitelist/blacklist clicks and reports errors", async () => {
  const h = harness();
  function show() {
    h.message({ type: "analysis", url: h.location.href, result: { verdict: "dangerous", reason: "Warning" } });
    return h.root.children[0].shadow.children[1];
  }
  let panel = show();
  await panel.children[5].events.click({ isTrusted: false });
  assert.equal(h.posts.length, 0);
  h.response = { ok: true };
  await panel.children[5].events.click({ isTrusted: true });
  assert.equal(h.posts[0].type, "setDomainPolicy");
  assert.equal(h.posts[0].policy, "whitelist");
  assert.equal(h.posts[0].url, h.location.href);
  assert.equal(h.root.children.length, 0);
  panel = show();
  await panel.children[6].events.click({ isTrusted: true });
  assert.equal(h.posts.at(-1).policy, "blacklist");
  assert.match(panel.children[7].textContent, /added to your blacklist/);
  h.response = { ok: false };
  await panel.children[5].events.click({ isTrusted: true });
  assert.match(panel.children[7].textContent, /Could not save/);
  assert.equal(panel.children[5].disabled, false);
  const before = h.posts.length;
  h.location.href = "https://other.invalid/";
  await panel.children[5].events.click({ isTrusted: true });
  assert.equal(h.posts.length, before);
});
