const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../extension/options.js"), "utf8");

async function harness(url = "https://Accounts.Example.com/login?private=not-saved") {
  const h = { posts: [], saved: [], closed: false, optionsOpened: false };
  function element() {
    return { children: [], events: {}, dataset: {}, value: "", disabled: true,
      append(...children) { this.children.push(...children); }, replaceChildren() { this.children = []; },
      addEventListener(name, fn) { this.events[name] = fn; }, setAttribute() {},
    };
  }
  const ids = ["domain-form", "status", "entries", "domain", "policy", "refresh", "current-domain",
    "current-rule", "allow-current", "block-current", "remove-current", "inspect-page", "open-options"];
  h.elements = Object.fromEntries(ids.map(id => [id, element()]));
  h.elements["domain-form"].querySelector = () => element();
  vm.runInNewContext(source, { URL: require("node:url").URL,
    document: { querySelector: id => h.elements[id.slice(1)], createElement: element },
    window: { close() { h.closed = true; } },
    chrome: {
      tabs: { query: async () => [{ id: 7, url }] },
      runtime: {
        openOptionsPage: async () => { h.optionsOpened = true; },
        async sendMessage(message) {
          h.posts.push(message);
          if (h.offline) return { ok: false, error: "Local server unavailable" };
          if (message.type === "inspectActive") return h.inspectError
            ? { ok: false, error: "Page changed" } : { ok: true };
          if (message.method === "GET") return { ok: true, entries: h.saved };
          if (message.method === "DELETE") {
            h.saved = h.saved.filter(entry => entry.hostname !== message.domain);
            return { ok: true, hostname: message.domain };
          }
          h.saved = [{ hostname: message.domain, policy: message.policy }];
          return { ok: true, ...h.saved[0] };
        },
      },
    },
  });
  await new Promise(resolve => setImmediate(resolve));
  h.click = id => h.elements[id].events.click();
  return h;
}

test("popup identifies the current domain and can whitelist, blacklist and remove it", async () => {
  const h = await harness();
  assert.equal(h.elements["current-domain"].textContent, "accounts.example.com");
  assert.equal(h.elements.domain.value, "accounts.example.com");
  assert.equal(h.elements["allow-current"].disabled, false);
  assert.equal(h.elements["remove-current"].disabled, true);
  await h.click("allow-current");
  assert.equal(h.saved[0].hostname, "accounts.example.com");
  assert.equal(h.saved[0].policy, "whitelist");
  assert.equal(h.elements["allow-current"].disabled, true);
  assert.equal(h.elements["block-current"].disabled, false);
  assert.match(h.elements["current-rule"].textContent, /whitelist/);
  await h.click("block-current");
  assert.equal(h.saved[0].policy, "blacklist");
  assert.equal(h.elements["block-current"].disabled, true);
  await h.click("remove-current");
  assert.equal(h.saved.length, 0);
  assert.match(h.elements["current-rule"].textContent, /No saved decision/);
});

test("popup inspects the captured current tab and opens the full manager", async () => {
  const h = await harness();
  h.inspectError = true;
  await h.click("inspect-page");
  assert.equal(h.closed, false);
  assert.equal(h.elements.status.textContent, "Page changed");
  h.inspectError = false;
  await h.click("inspect-page");
  assert.equal(h.closed, true);
  const message = h.posts.find(post => post.type === "inspectActive");
  assert.equal(message.tabId, 7);
  assert.match(message.url, /^https:\/\/Accounts.Example.com/);
  await h.click("open-options");
  assert.equal(h.optionsOpened, true);
});

test("popup disables current-page actions on restricted pages", async () => {
  const h = await harness("chrome://extensions/");
  assert.equal(h.elements["current-domain"].textContent, "No supported page");
  for (const id of ["allow-current", "block-current", "remove-current", "inspect-page"]) {
    assert.equal(h.elements[id].disabled, true);
  }
});

test("popup reports failed saves and can refresh lists after the server recovers", async () => {
  const h = await harness();
  h.offline = true;
  await h.click("allow-current");
  assert.equal(h.elements.status.textContent, "Local server unavailable");
  assert.equal(h.saved.length, 0);
  await h.click("refresh");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.elements["allow-current"].disabled, true);
  h.offline = false;
  await h.click("refresh");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.elements["allow-current"].disabled, false);
});
