# NoPhish — Development Plan

## Objective

Build a **small, local phishing-detection prototype**: a Chrome extension and a local Python server. The server first uses **EasyOCR with CUDA GPU inference** to decide whether a screenshot shows a login page. **Only if it does**, a small local vision-language model (VLM) identifies the apparent brand. The server then compares the URL against approved domains for that brand and returns a verdict to the extension.

**Keep this project very small.** There is no need for a complex architecture, database, cloud service, model training, UI framework, or large collection of helper classes. Prefer direct, readable code. **Implement only the assigned milestone, test it, report results, and stop.**

## Required pipeline

```text
Chrome MV3 extension
    | capture visible tab screenshot + URL on relevant page/navigation events
    v
Local Python FastAPI server (127.0.0.1)
    |
    v
EasyOCR (GPU/CUDA) — extract visible text
    |
    v
Login-page keyword/rule check
    |-- NOT a login page --> stop; return not_login (no VLM call)
    |-- login page -----> continue
                             |
                             v
                    Local small VLM (GPU/CUDA)
                    brand: microsoft / google / meta / unknown
                             |
                             v
                    Strict URL hostname verification
                             |
                             v
                    verdict: safe / dangerous / unknown
                             |
                             v
                    Extension displays warning if dangerous
```

**OCR is a mandatory gate, not a later optional feature.** Never call the VLM for a page that did not pass the OCR login-page check. The login check is deliberately simple, using OCR text and a compact set of login-related keywords/phrases. Do not build a separate learned login classifier.

## Environment and model choices

- **Development target:** Windows 11 laptop with an **NVIDIA RTX 3080 Ti Laptop GPU (16 GB VRAM)**. Both EasyOCR and the VLM must perform inference locally on CUDA, not cloud APIs.
- **OCR:** `easyocr.Reader(['en'], gpu=True)`, created once at server startup. Extract text from the screenshot, then normalise and inspect it for login-related terms such as `sign in`, `log in`, `login`, `password`, `email address`, or `username`. Use a straightforward small rule set; not every lone word should necessarily qualify. Keep keywords easy to edit. Record whether a page passed this gate. If OCR fails, return `unknown` (analysis error), not `not_login` or `safe`.
- **VLM:** start with **Qwen3-VL-2B** using a simple maintained CUDA-compatible inference implementation. Load the model once and reuse it. Try **SmolVLM2-500M** only during evaluation if useful. No custom training initially.
- **Brands:** `microsoft`, `google`, `meta`, `unknown`. A phishing site will usually imitate the original site's clear logo/layout, so this is a constrained visual classification task. Instruct the VLM to return exactly one label. Invalid/unparseable responses become `unknown`.
- **No transmission:** screenshots and URLs stay on the computer, moving only between the extension and local server. Do not log screenshots, extracted OCR text, passwords, credentials, full URLs with query strings, or tokens.
- **Inference order is fixed:** OCR → login gate → VLM → hostname verification. Avoid running models on ordinary pages unnecessarily.

## Minimal components

```text
extension/
  manifest.json
  background.js           # capture screenshot/URL, call local server, display result
  content.js              # minimal signal for when to inspect/reinspect a page
  [small warning UI files only if actually needed]
server/
  main.py                 # FastAPI endpoint, orchestrates sequential stages
  ocr.py                  # GPU EasyOCR + simple login keyword logic
  classifier.py           # local GPU VLM brand classification
  domains.py              # static brand -> approved hostnames, strict URL matching
requirements.txt
README.md
plan.md
```

This is a suggested file layout, **not** a request to build an extensible framework. Combine files if doing so is clearer. No database, Docker, queues, task workers, authentication system, frontend framework, monitoring stack, or network threat feeds.

The extension may use cheap DOM signals (e.g. password inputs, input focus, navigation) **to decide when to capture and send a page for analysis**, but DOM detection must **not replace the mandatory EasyOCR login-page gate**. A fake page may have custom form elements, so do not make a password field a strict prerequisite for every screenshot. Implement simple triggers and deduplication, without continuous screenshot polling.

## Detection and verdict rules

1. Extension captures the **visible tab** screenshot and current HTTP(S) URL when inspection is warranted. It must never read password values or other form-field values.
2. Extension POSTs screenshot + URL to local `/analyze`. Validate the URL and request size/image format; return clear errors without crashing.
3. Server runs **GPU EasyOCR first**. Normalise recognised text (lowercase, whitespace). Use a small, explicit set of rules/keywords to decide `is_login_page`.
4. If **not** a login page: return `{ "verdict": "not_login", "brand": null, "reason": "No login page detected" }`; **do not invoke the VLM**. `not_login` means the detector did not identify a login page, **not** that the website is safe.
5. If it **is** a login page: pass the same screenshot to the local GPU VLM. Request one of `microsoft`, `google`, `meta`, `unknown` using a fixed prompt. Do not let website text control model instructions; constrain and validate the response.
6. Parse and normalise the hostname with a standard URL parser. Check it against an explicit static allowlist for the identified brand. Use exact hostname matching or explicitly allowed subdomains only; **never substring matching** (`microsoft.com.attacker.example` must not match). HTTPS alone is not proof of trust.
7. Return `dangerous` for a **known brand on an unapproved hostname**, `safe` for a known brand on an explicitly approved hostname, and `unknown` if the brand is unknown, OCR/VLM fails, or analysis is inconclusive. `safe` means this specific brand-domain consistency check passed; it does not guarantee the page is harmless.
8. Extension shows a noticeable warning for `dangerous`, a non-alarming inconclusive state for `unknown`, and minimal/no UI for `safe` or `not_login`. Do not automatically block navigation or submit forms.

**Keep OCR rules conservative enough to avoid analysing every page with the word “email”, but simple enough that typical sign-in pages pass.** Test keyword behaviour with a few screenshots before making rules more sophisticated. A false negative in OCR means the VLM is skipped; this is an acknowledged limitation to measure, not a reason to introduce a complex pipeline prematurely.

## Milestones

### M1 — Extension ↔ local server (mock analysis)

**Implement:**
- Minimal Chrome Manifest V3 extension and local FastAPI `/analyze` endpoint.
- Capture a visible tab screenshot and its URL; send to `127.0.0.1` on sensible page/navigation triggers.
- Return a mock JSON result and show it through minimal extension UI or a simple console-visible result during development.
- Handle server-offline, permissions and duplicate events without noisy failures.

**Acceptance criteria:** A chosen test page produces a request with the correct URL and valid screenshot, and the extension receives the mock result. No real OCR or VLM yet. Stop after M1.

### M2 — Mandatory EasyOCR GPU login-page detection

**Implement:**
- Initialise EasyOCR once with `gpu=True`; verify it actually uses CUDA.
- Decode screenshot on the server, extract OCR text, normalise it, and apply simple login-related keyword/phrase rules.
- Return `not_login` and terminate analysis when the page fails the OCR gate. For passing pages, return a **temporary mock `login_detected` result** until M3.
- Add a few sample screenshots of ordinary pages and real login pages, plus simple repeatable tests. Handle OCR errors as `unknown`, not `not_login`.

**Acceptance criteria:** Typical sign-in screenshots pass; ordinary non-login screenshots return `not_login`; running a non-login screenshot never invokes the VLM. Verify EasyOCR ran on the NVIDIA GPU. **This stage must exist before VLM integration.** Stop after M2.

### M3 — Local GPU VLM brand recognition, behind OCR

**Implement:**
- Load one small VLM once with CUDA (start with Qwen3-VL-2B).
- For OCR-confirmed login screenshots only, classify brand as `microsoft`, `google`, `meta`, or `unknown`.
- Parse model output strictly, handle errors as `unknown`, and report approximate inference time.
- Reuse the M2 screenshots and add relevant known-brand login screenshots. Ensure `not_login` still bypasses the VLM.

**Acceptance criteria:** Brand detection works locally on the GPU for sample login screenshots, and a rejected OCR screenshot causes **zero VLM calls**. Stop after M3.

### M4 — Strict domain matching and warning UI

**Implement:**
- Small brand-to-authorised-hostname map for the supported brands.
- Robust hostname parsing and exact/approved-subdomain matching; test lookalikes, malicious suffixes, and legitimate hosts.
- Return `safe`, `dangerous`, `unknown`, or `not_login`, and display a clear extension warning for `dangerous`.
- Debounce/deduplicate requests; handle SPA navigation and local-server errors in a simple manner.

**Acceptance criteria:** Microsoft-branded login page on an unapproved hostname is `dangerous`; the same screenshot on a correctly approved Microsoft host is `safe`; unrecognised brands are `unknown`; ordinary pages remain `not_login`. Stop after M4.

### M5 — Small evaluation, cleanup and instructions

**Implement:**
- Test real login-page screenshots and visually copied phishing examples for the three supported brands, plus ordinary pages and unrelated login pages.
- Measure **OCR login-gate pass/miss rate**, brand accuracy on pages that passed OCR, false warnings, and approximate end-to-end timings. Include GPU inference time for both EasyOCR and VLM.
- Optionally compare SmolVLM2-500M on the **same** passing-login screenshots, only if speed or model size warrants it.
- Write concise README instructions for installing dependencies, starting the local server, loading the extension, and testing. Include known limitations.

**Acceptance criteria:** README explains the verified OCR → VLM → domain-verification pipeline, basic performance, observed misses and how to run it. No large test harness or speculative optimisation. Stop after M5.

## Rules for the coding agent

1. **Do one milestone at a time. Stop after finishing it.** Report files changed, test commands and outcomes, then await the next instruction.
2. **Do not overcomplicate the project.** Use small functions, few files and no unnecessary abstractions. Avoid speculative scalability or production infrastructure.
3. **EasyOCR is mandatory and precedes the VLM.** Use GPU inference for **both**. If no login page is detected, exit early without invoking the VLM.
4. **Do not silently substitute the DOM or VLM for EasyOCR.** DOM heuristics are only for economical capture triggers. If EasyOCR cannot use CUDA, report the issue instead of silently reverting to CPU.
5. **No cloud services, training pipeline or external screenshot uploads.** This prototype is entirely local.
6. **Never read form values or store screenshots.** Log only limited diagnostics (stage/verdict/timing), without sensitive data.
7. **Fail conservatively.** Errors and uncertainty are `unknown`, not `safe`. `not_login` is specifically a completed OCR check finding insufficient login evidence.
8. **Keep verification deterministic.** The VLM identifies brand; Python URL matching judges domain consistency. Never let the VLM decide URL legitimacy.
9. **Prioritise working end-to-end software over UI polish.** No dashboards, account systems, browser blocking, persistent database or complicated settings.

## Explicitly out of scope

Cloud inference, custom model training, a separate ML login-page classifier, enterprise-grade phishing feeds, history/database storage, OCR-based credential extraction, automatic blocking, elaborate settings pages, cross-browser support, background screenshot polling, or broad feature expansion.

**Final target:** a compact, understandable prototype implementing **screenshot + URL → GPU EasyOCR login-page gate → GPU VLM brand recognition (only for detected login pages) → strict brand/domain verification → browser warning**.
