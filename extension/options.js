const form = document.querySelector("#domain-form");
const status = document.querySelector("#status");
const entries = document.querySelector("#entries");
const currentDomain = document.querySelector("#current-domain");
let currentTab;
let currentHost;
let savedEntries = [];
let currentBusy = false;
let listsAvailable = false;

function updateCurrent() {
  if (!currentDomain) return;
  const policy = savedEntries.find(entry => entry.hostname === currentHost)?.policy;
  document.querySelector("#current-rule").textContent = !currentHost ? "Open an HTTP(S) page to use these actions."
    : !listsAvailable ? "Domain lists unavailable. Start the local server and refresh."
    : policy ? `On your ${policy}. Model analysis is skipped.` : "No saved decision. Normal analysis applies.";
  for (const [id, disabled] of [["#allow-current", policy === "whitelist"],
                               ["#block-current", policy === "blacklist"], ["#remove-current", !policy]]) {
    document.querySelector(id).disabled = currentBusy || !currentHost || !listsAvailable || disabled;
  }
  document.querySelector("#inspect-page").disabled = !currentHost || currentBusy;
}

function notify(text, error = false) {
  status.textContent = text;
  status.dataset.error = String(error);
}

async function request(method, domain, policy) {
  const response = await chrome.runtime.sendMessage({ type: "domainPolicies", method, domain, policy });
  if (!response?.ok) throw new Error(response?.error || "Local server unavailable");
  return response;
}

async function refresh() {
  let result;
  try { result = await request("GET"); listsAvailable = true; }
  catch (error) { listsAvailable = false; updateCurrent(); throw error; }
  savedEntries = result.entries;
  updateCurrent();
  entries.replaceChildren();
  for (const entry of result.entries) {
    const row = document.createElement("tr");
    const host = document.createElement("td");
    host.textContent = entry.hostname;
    const policy = document.createElement("td");
    policy.textContent = entry.policy;
    const action = document.createElement("td");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${entry.hostname} from ${entry.policy}`);
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        await request("DELETE", entry.hostname);
        await refresh();
        notify(`Removed ${entry.hostname}. Normal analysis will resume.`);
      } catch (error) { notify(error.message, true); }
      finally { remove.disabled = false; }
    });
    action.append(remove);
    row.append(host, policy, action);
    entries.append(row);
  }
  if (!result.entries.length) notify("No saved domains. Pages use normal OCR and brand analysis.");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = form.querySelector("button");
  submit.disabled = true;
  try {
    const domain = document.querySelector("#domain").value.trim();
    const policy = document.querySelector("#policy").value;
    const result = await request("PUT", domain, policy);
    await refresh();
    notify(`Saved ${result.hostname} to your ${result.policy}.`);
    document.querySelector("#domain").value = "";
  } catch (error) { notify(error.message, true); }
  finally { submit.disabled = false; }
});

document.querySelector("#refresh").addEventListener("click", () => {
  refresh().then(() => notify("Domain lists refreshed.")).catch((error) => notify(error.message, true));
});
refresh().catch((error) => notify(error.message, true));

if (currentDomain) {
  chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
    if (!tab?.url || !/^https?:\/\//i.test(tab.url)) {
      currentDomain.textContent = "No supported page";
      updateCurrent();
      return;
    }
    currentTab = tab;
    currentHost = new URL(tab.url).hostname.toLowerCase().replace(/\.$/, "").replace(/^\[|\]$/g, "");
    currentDomain.textContent = currentHost;
    document.querySelector("#domain").value = currentHost;
    updateCurrent();
  }).catch(() => {
    currentDomain.textContent = "Current tab unavailable";
    updateCurrent();
  });

  async function decideCurrent(policy) {
    if (!currentHost || currentBusy) return;
    currentBusy = true;
    updateCurrent();
    try {
      const result = await request(policy ? "PUT" : "DELETE", currentHost, policy);
      await refresh();
      notify(policy ? `Saved ${result.hostname} to your ${result.policy}.`
        : `Removed ${result.hostname}. Normal analysis will resume.`);
    } catch (error) { notify(error.message, true); }
    finally { currentBusy = false; updateCurrent(); }
  }
  document.querySelector("#allow-current").addEventListener("click", () => decideCurrent("whitelist"));
  document.querySelector("#block-current").addEventListener("click", () => decideCurrent("blacklist"));
  document.querySelector("#remove-current").addEventListener("click", () => decideCurrent(null));
  document.querySelector("#inspect-page").addEventListener("click", async () => {
    if (!currentHost || currentBusy) return;
    try {
      const result = await chrome.runtime.sendMessage({ type: "inspectActive", tabId: currentTab.id, url: currentTab.url });
      if (!result?.ok) throw new Error(result?.error || "Could not inspect this page");
      window.close();
    } catch (error) { notify(error.message, true); }
  });
  document.querySelector("#open-options").addEventListener("click", () => {
    chrome.runtime.openOptionsPage().catch(() => notify("Could not open the domain manager.", true));
  });
}
