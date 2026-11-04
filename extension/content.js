// DOM signals only schedule captures. The mandatory OCR gate runs on the server.
(() => {
  let lastSignature = null;
  let pageUrl = location.href;
  let loginSeen = false;
  let timer;
  let notice;

  function clearNotice() {
    notice?.remove();
    notice = null;
  }

  function checkPage() {
    timer = null;
    if (location.href !== pageUrl) {
      pageUrl = location.href;
      loginSeen = false;
      clearNotice();
    }
    if (document.visibilityState !== "visible") return;
    loginSeen ||= [...document.querySelectorAll('input[type="password"]')].some((field) =>
      field.getClientRects().length && getComputedStyle(field).visibility !== "hidden");
    const signature = `${pageUrl}:${loginSeen}`;
    if (signature === lastSignature) return;
    lastSignature = signature;
    // Never inspect input values, credentials, or page text.
    chrome.runtime.sendMessage({ type: "inspect", login: loginSeen }).catch(() => {});
  }

  function schedule() {
    if (!timer) timer = setTimeout(checkPage, 350);
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "clearNotice") {
      clearNotice();
      schedule();
    }
    if (message?.type !== "analysis" || message.url !== location.href) return;
    clearNotice();
    if (message.result?.verdict !== "dangerous") return;
    notice = document.createElement("div");
    notice.style.cssText = "all:initial;position:fixed;top:12px;right:12px;z-index:2147483647";
    const root = notice.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = `
      section{box-sizing:border-box;width:420px;max-width:calc(100vw - 24px);padding:18px;
        background:#991b1b;color:#fff;border:2px solid #fecaca;border-radius:8px;
        box-shadow:0 6px 24px #0006;font:15px/1.5 system-ui,sans-serif;text-align:left}
      strong{display:block;font-size:19px}p{margin:10px 0;overflow-wrap:anywhere}button{padding:6px 10px;margin:3px;
        border:1px solid #fff;border-radius:4px;background:#fff;color:#7f1d1d;cursor:pointer}
      button:disabled{opacity:.65;cursor:wait}
      .reason{padding:10px 12px;background:#ffffff14;border-radius:5px}
      .reason::before{content:"Why this warning?";display:block;font-weight:700;margin-bottom:4px}`;
    const panel = document.createElement("section");
    panel.setAttribute("role", "alert");
    const heading = document.createElement("strong");
    const blacklisted = message.result.source === "user_list" && message.result.policy === "blacklist";
    heading.textContent = blacklisted ? "NoPhish: blacklisted domain" : "NoPhish: possible phishing page";
    const reason = document.createElement("p");
    reason.setAttribute("class", "reason");
    reason.textContent = blacklisted
      ? "This domain is on your blacklist. You marked it as dangerous."
      : message.result.reason;
    const advice = document.createElement("p");
    advice.textContent = blacklisted
      ? "If this decision is incorrect, remove it in the extension popup or whitelist the domain."
      : "Check the address before entering credentials. This prototype can make mistakes.";
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.textContent = "Dismiss warning";
    dismiss.addEventListener("click", clearNotice);
    panel.append(heading, reason, advice, dismiss);
    const domain = document.createElement("p");
    domain.textContent = `Your decision applies to this exact domain: ${new URL(message.url).hostname}`;
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    const whitelist = document.createElement("button");
    const blacklist = document.createElement("button");
    whitelist.type = blacklist.type = "button";
    whitelist.textContent = "Trust domain — whitelist";
    blacklist.textContent = "Confirm danger — blacklist";
    blacklist.disabled = message.result.policy === "blacklist";
    async function decide(policy, event) {
      if (!event.isTrusted || location.href !== message.url) return;
      whitelist.disabled = blacklist.disabled = true;
      status.textContent = "Saving domain decision…";
      try {
        const response = await chrome.runtime.sendMessage({ type: "setDomainPolicy", url: message.url, policy });
        if (!response?.ok) throw new Error();
        if (location.href !== message.url) return;
        if (policy === "whitelist") clearNotice();
        else status.textContent = "Domain added to your blacklist. Manage entries in the extension options.";
      } catch {
        status.textContent = "Could not save. Check that the local server is running and try again.";
      } finally {
        whitelist.disabled = false;
        blacklist.disabled = message.result.policy === "blacklist";
      }
    }
    whitelist.addEventListener("click", (event) => decide("whitelist", event));
    blacklist.addEventListener("click", (event) => decide("blacklist", event));
    panel.append(domain, whitelist, blacklist, status);
    root.append(style, panel);
    document.documentElement.append(notice);
  });

  schedule();
  for (const event of ["pageshow", "popstate", "hashchange"]) addEventListener(event, schedule);
  document.addEventListener("visibilitychange", schedule);
  new MutationObserver(schedule).observe(document.documentElement, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ["type", "hidden", "style", "class"],
  });
})();
