const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../extension/options.js"), "utf8");

test("options page adds, lists and removes domains using literal text and shows save errors", async () => {
  const posts = [];
  let saved = [];
  let fail = false;
  function element() {
    return { children: [], events: {}, dataset: {}, value: "", attributes: {},
      append(...children) { this.children.push(...children); },
      replaceChildren() { this.children = []; },
      addEventListener(event, handler) { this.events[event] = handler; },
      setAttribute(key, value) { this.attributes[key] = value; },
    };
  }
  const elements = Object.fromEntries(["#domain-form", "#status", "#entries", "#domain", "#policy", "#refresh"].map(id => [id, element()]));
  const submit = element();
  elements["#domain-form"].querySelector = () => submit;
  vm.runInNewContext(source, { document: { querySelector: id => elements[id], createElement: element },
    chrome: { runtime: { async sendMessage(message) {
      posts.push(message);
      if (fail) return { ok: false, error: "Server unavailable" };
      if (message.method === "GET") return { ok: true, entries: saved };
      if (message.method === "DELETE") { saved = []; return { ok: true }; }
      saved = [{ hostname: message.domain, policy: message.policy }];
      return { ok: true, ...saved[0] };
    } } },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(elements["#status"].textContent, /No saved domains/);
  elements["#domain"].value = "example.com";
  elements["#policy"].value = "whitelist";
  await elements["#domain-form"].events.submit({ preventDefault() {} });
  assert.equal(posts.find(p => p.method === "PUT").domain, "example.com");
  const row = elements["#entries"].children[0];
  assert.equal(row.children[0].textContent, "example.com");
  assert.equal(row.children[0].innerHTML, undefined);
  assert.equal(row.children[1].textContent, "whitelist");
  await row.children[2].children[0].events.click();
  assert.equal(posts.find(p => p.method === "DELETE").domain, "example.com");
  assert.equal(elements["#entries"].children.length, 0);
  fail = true;
  elements["#domain"].value = "other.example";
  await elements["#domain-form"].events.submit({ preventDefault() {} });
  assert.equal(elements["#status"].textContent, "Server unavailable");
  assert.equal(submit.disabled, false);
});
