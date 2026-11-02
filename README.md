# NoPhish

![NoPhish banner](banner.png)

A local Chrome MV3 prototype with user-managed domain decisions:
**visible screenshot + URL → SQLite whitelist/blacklist → CUDA EasyOCR → English login gate → CUDA
Qwen3-VL-2B through local Ollama → exact hostname check → browser warning**.
Listed domains skip both models. For unlisted domains, rejected OCR gates skip
the VLM. EasyOCR loads once; Ollama preloads and retains
the installed brand model. CPU/partial model offload is rejected; no cloud inference.

| Verdict | Meaning and display |
| --- | --- |
| `dangerous` | User-blacklisted domain, or known brand on an unapproved host; red `!` and dismissible notice |
| `safe` | User-whitelisted domain, or known brand on an approved host; no warning |
| `unknown` | Unsupported/uncertain brand or model failure; `?` |
| `not_login` | OCR found insufficient login evidence; no warning |

Neither `safe` nor `not_login` guarantees a harmless page. NoPhish never blocks
navigation, submits forms, or reads input values. Runtime screenshots, OCR text,
credentials and URLs are never saved or logged. Only public test images are saved.
Package/weight downloads need internet; inference stays local.

## Your whitelist and blacklist

Every analysis checks the normalized hostname in local SQLite **before image
decoding, OCR, VLM inference or model-availability checks**. A whitelist hit
immediately returns `safe`; a blacklist hit returns `dangerous`, both with
`brand: null` and `source: user_list`. Unlisted domains follow the normal pipeline.

When a warning appears, choose **Trust domain — whitelist** if it is a false
warning, or **Confirm danger — blacklist** to remember it as dangerous. The
warning names the exact domain affected. Saving a whitelist decision clears
the warning/badge immediately; blacklisting keeps the warning. A model verdict
alone never adds an entry. Only an explicit user action saves a decision.

Click the NoPhish toolbar icon to open the **domain-list popup**. It shows the
current hostname and whether it is whitelisted or blacklisted, with quick
**Whitelist**, **Blacklist** and **Remove entry** actions. Add any hostname or
HTTP(S) URL using the form, and remove saved domains directly from the list.
The current-domain actions are disabled on unsupported pages such as
`chrome://extensions`; manual domain management is still available there.
Choose **Inspect page** for a manual inspection/retry (the popup closes
when inspection is scheduled). Click **Open full manager** for the larger
options page; it is also available from the extension's Details page.

The popup and options page share the same SQLite entries and accept a hostname
or HTTP(S) URL, displaying the normalized
hostname actually saved. Saving the other decision replaces the previous one;
removing an entry restores normal model analysis. No restart is needed for edits.

Entries persist in the ignored `.data/domains.sqlite3`, storing only hostname
and decision. Matching is exact: `example.com` does not include `www.example.com`
or `example.com.attacker.invalid`. Paths, ports, queries and fragments are not
stored. User decisions override the built-in brand map; a whitelist is your
instruction to bypass analysis, not a guarantee the site is harmless. Database
errors return inconclusive `unknown` without running inference.

The local API is `GET /domain-policies` to list entries; `PUT /domain-policies`
with JSON `{"domain":"example.com","policy":"whitelist"}` (or `blacklist`) to
save; `DELETE /domain-policies` with `{"domain":"example.com"}` to remove.
Writes require `Content-Type: application/json` and
`X-NoPhish-Action: domain-policy`; ordinary website origins are rejected and
CORS is not enabled. The extension sends these requests from its background
worker. Restart the Python server and reload the extension/test tabs after
installing this update.

## Install (PowerShell, repository root)

Use **Python 3.11**, an NVIDIA GPU and a driver supporting CUDA 12.8 wheels.
Verified on an RTX 3080 Ti Laptop GPU (16 GiB). Install CUDA wheels first
([EasyOCR Windows setup](https://github.com/JaidedAI/EasyOCR#installation),
[PyTorch commands](https://pytorch.org/get-started/previous-versions/)):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
```

Alternatively use `uv venv --python 3.11`, then `uv pip install --python
.venv\Scripts\python.exe` with the same package/index arguments and requirements.
Do not reuse a venv created with another Python version: compiled packages may
remain. Stop the server and use `uv venv --python 3.11 --clear .venv` to replace
an existing mixed environment, then repeat both dependency installations.

Download OCR weights once (Torch itself is approximately 2.6 GiB):

```powershell
.\.venv\Scripts\python.exe -m server.ocr --download-models --check-samples
```

OCR weights are cached in `.models/easyocr`. Install/start
[Ollama for Windows](https://docs.ollama.com/windows) and separately download
the model selected by `MODEL_ID` in [server/classifier.py](server/classifier.py)
(currently **`qwen3.5:2b`**; model installation is user-managed). Change that
constant and restart the Python server to select another installed vision model;
selecting a model in Ollama alone does not change NoPhish's API requests.
NoPhish calls only `http://127.0.0.1:11434`: empty-prompt `/api/generate` at
startup to preload, `/api/chat` for classification, and `/api/ps` to verify GPU
residency. It never invokes a pull/download endpoint or follows redirects, and
ignores HTTP proxy environment settings. The previous Transformers cache at
`.models/qwen3-vl-2b` is no longer used; Transformers is no longer a dependency.

Requests use a fixed prompt, `temperature: 0`, `num_predict: 8`, `think: false`,
`stream: false`, and `keep_alive: -1` to retain the model. Only the image goes
to Ollama, resized in memory to fit 1280 × 1280; never the page URL or OCR text.
Only exact supported labels are accepted. Ollama must report the model fully
resident in VRAM; missing placement data, CPU/partial offload, timeouts and
invalid responses fail to `unknown`. Startup preload has a 60-second timeout;
classification requests have an 8-second timeout. If setup was incomplete at
startup, finish it and restart NoPhish. Working OCR can still return `not_login`
when Ollama is unavailable. Check `ollama ps` for GPU placement.

## Start and load

```powershell
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Wait for `EasyOCR ready: detector and recognizer on CUDA` and
`Ollama ready: local brand model fully GPU-resident`. Use one worker and loopback only.
GPU stages share a lock and run outside the event loop. Request limit: 8 MiB;
PNG/JPEG limit: 6 MiB and 20 million pixels. Images are decoded in memory.
Chrome captures at the native visible-tab resolution (viewport/display scaling
dependent). OCR now fits that image inside **1280 × 720**, preserving aspect ratio
and never upscaling, and recognizes text boxes in batches of 16. Dimension-only
diagnostics show the original and OCR input sizes. The VLM retains its separate
1280 × 1280 cap and receives the original screenshot bytes.

1. Open `chrome://extensions`, enable Developer mode, **Load unpacked** the
   `extension` folder and pin NoPhish. Reload the extension and test tabs after updates.
2. Automatic [visible-tab capture](https://developer.chrome.com/docs/extensions/reference/api/tabs#method-captureVisibleTab)
   requires `<all_urls>`; `webNavigation` tracks SPA routes. Content scripts run
   on HTTP(S) only. Analysis goes solely to `http://127.0.0.1:8000/analyze`.
3. Serve the public fixtures in another terminal:

   ```powershell
   .\.venv\Scripts\python.exe -m http.server 8001 --bind 127.0.0.1 --directory tests
   ```

4. Open `http://127.0.0.1:8001/demo.html?view=account`: expect a red `!` and
   **NoPhish: possible phishing page** for the copied Microsoft image on localhost.
   Dismiss the notice; the badge remains. Choose **News view** to clear it, then
   **Account view**, Back/Forward and **Change route only** to check SPA handling.
5. Check `/login.html` for `unknown` and `/ordinary.html` for `not_login`.
   Stop the server to check `OFF`; capture/response errors show `ERR`.
   Click the toolbar icon and choose **Inspect page** to retry. No forms need to be filled in.

Load, activation, navigation and a newly visible password field trigger debounced
inspection; a password field is not required. Duplicate events coalesce and stale
results are discarded. No screenshot polling. Capture throttle: 1.1 s; timeout:
10 s; automatic offline cooldown: 15 s (manual retry bypasses it). Debounce adds
roughly 0.6 s, plus the content script's 0.35 s where applicable; busy periods
and throttling can add more.

## Hostname policy

[server/domains.py](server/domains.py) uses these exact hosts:

| Brand | Approved hosts |
| --- | --- |
| microsoft | `login.microsoftonline.com`, `login.live.com`, `login.microsoft.com` |
| google | `accounts.google.com` |
| meta | `facebook.com`, `www.facebook.com`, `m.facebook.com`, `instagram.com`, `www.instagram.com`, `web.whatsapp.com` |

Parsing normalizes case, one trailing root dot and IDNA/UTS #46 names, rejecting
malformed/ambiguous hosts. It checks the parsed hostname, never a substring,
user info or query. HTTPS, ports and paths do not establish trust. No inferred
subdomains or remote lookups. Explicit user-list decisions are checked first.
References: [Microsoft](https://support.microsoft.com/en-us/office/troubleshoot-sign-in-issues-for-support-microsoft-com),
[login.microsoft.com](https://learn.microsoft.com/en-us/microsoft-365/copilot/add-copilot-endpoints-allowlist),
[Google](https://support.google.com/chrome/answer/7299973),
[Facebook](https://www.facebook.com/login/), [Instagram](https://www.instagram.com/accounts/login/),
[WhatsApp Web](https://blog.whatsapp.com/whats-app-web).

## Evaluation and checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
node --test tests/extension.test.cjs tests/content.test.cjs tests/options.test.cjs tests/popup.test.cjs
.\.venv\Scripts\python.exe -m server.classifier --check-samples
.\.venv\Scripts\python.exe tests/evaluate.py
.\.venv\Scripts\python.exe tests/benchmark_ocr.py
```

Node.js is needed only for extension tests. Unit tests mock Ollama HTTP responses
and require no downloaded VLM. The sample check/evaluation requires cached CUDA
OCR weights and a running local Ollama with the installed model; it overwrites
[tests/evaluation-results.json](tests/evaluation-results.json) with limited metadata,
labels, call counts and timings. Detection misses are reported; unavailable
models, endpoint errors and gate/call-order violations abort the run.

After the Ollama migration, 29 Python tests passed, covering the HTTP/image
contract, strict labels, preload/keep-alive, missing models, timeouts, invalid
responses, GPU-placement rejection and the unchanged OCR/domain pipeline.
The domain-list update adds persistence, exact matching, replacement/removal,
pre-inference bypass, mutation validation, and warning/options action tests.
**35 Python tests and 26 extension/content/options/popup tests pass.** The popup
tests cover current-domain selection, whitelist/blacklist replacement and
removal, restricted pages, server errors and manual inspection. The new warning
and management page were also rendered in isolated headless Chrome using public
fixtures and simulated extension messaging. The actual background script also
passed a live localhost HTTP check against an isolated FastAPI/SQLite server:
whitelist returned safe, blacklist dangerous, and removal restored normal
analysis, with both models unavailable. Native extension installation and UI
clicks can be checked manually using the demo.
Historical results below remain the Transformers baseline, not current Ollama
accuracy or latency measurements.

### Historical Transformers baseline (not Ollama results)

The following evaluation was recorded before migration on 2 November 2026:
Python 3.11.15, Torch 2.11.0+cu128, EasyOCR 1.7.2, Transformers 5.22.1,
RTX 3080 Ti Laptop GPU, Qwen3-VL-2B-Instruct in BF16. Both OCR models and all
VLM parameters were verified on CUDA. These measurements do not establish
Ollama accuracy or speed; re-run evaluation after installing the Ollama model.

Evaluation: **11 images, 14 scenarios, three rounds / 42 requests**. Six public
documentation screenshots from development plus five new synthetic probes;
[fixture sources and labels](tests/samples/README.md). Each real Microsoft,
Google and Facebook image is replayed unchanged with an approved host and
`fixture.invalid`, simulating an exact visual copy. URLs are local request data;
no external site is contacted. This is not an independent accuracy benchmark or
collected phishing dataset: development images informed the prompt and the new
probes are synthetic.

| Measure (unique images/scenarios, first round) | Result |
| --- | --- |
| Login gate pass / miss | 5/7 passed (71.4%); 2/7 missed |
| Ordinary gate false passes | 1/4 (25%) |
| Brand accuracy on gate-passing login images | 5/5: 3 supported brands, 2 unknown |
| Supported-brand copies / approved counterparts | 3/3 dangerous; 3/3 safe |
| False warnings on benign scenarios | 0/11 |
| Expected final verdicts | 11/14 |
| VLM calls after OCR rejection | 0 across all 15 rejected requests |

Labels/verdicts stayed stable across rounds. Development gate checks: 4/4 logins
and 2/2 ordinary pages correct. New probes: 1/3 logins passed and 1/2 ordinary
pages rejected correctly. The French login and passwordless Continue form
were missed (`not_login`). An article's Log in navigation caused an extra VLM
call and `unknown`, without a warning. These failures were retained without
tuning the production rules against the evaluation probes.

| Warm latency (rounds 2–3) | Median | Nearest-rank p95 | Requests |
| --- | --- | --- | --- |
| EasyOCR GPU stage | 195 ms | 329 ms | 28 |
| Qwen GPU stage | 559 ms | 1,358 ms | 18 |
| API total, gate passed | 689 ms | 1,609 ms | 18 |
| API total, gate rejected | 250 ms | 263 ms | 10 |

These historical results include the OCR sizing/batching change below;
the VLM used Transformers. GPU stages use
CUDA-synchronized wall time including CPU preprocessing, not
kernel-only timings. API totals use in-process FastAPI TestClient, including
JSON handling, validation and analysis; excluding startup, socket transport,
browser capture and trigger/debounce delays. First-round timings are retained
but excluded from warm summaries. Small samples and laptop power/thermal state
limit comparisons. Qwen fits the target GPU. The optional SmolVLM comparison
has not been performed; no faster-model accuracy claim is made. Current Ollama
VLM timing measures preprocessing, HTTP, inference and GPU-placement checks;
its prompt/decode/load durations are separately logged from API nanoseconds.

### OCR sizing/batching comparison

[tests/ocr-performance.json](tests/ocr-performance.json) records a paired
comparison on the 11 public/synthetic images plus one synthetic 2560 × 1440
empty-field login layout. Three rounds, with rotated variant order and the first
round excluded from summaries; no user screenshots were stored.

| OCR settings | Warm median | Warm p95 |
| --- | --- | --- |
| Original size, batch 1 | 314 ms | 1,142 ms |
| Fit 1280 × 720, batch 1 | 306 ms | 990 ms |
| Fit 1280 × 720, batch 16 (current) | 211 ms | 348 ms |

The synthetic HiDPI case averaged 1,220 ms originally versus 327 ms with both
changes. Gate results matched the original in every paired check. Most existing
fixtures are already below 720p, so batching provides much of their improvement.
This measures complete GPU-backed OCR wall time, including CPU work, rather
than individual CUDA kernels. Lower resolution can still miss small text on
unseen pages. Restart the server to apply changes.

## Known limitations

The English gate accepts sign in / signin / log in / login, or password plus
an identity term. OCR errors, small text, other languages and passwordless flows
can skip brand analysis; ordinary login links can trigger it. Only visible
content is checked. The host map omits legitimate tenant, regional and SSO hosts,
so false warnings remain possible despite none in this tiny set. Brand labels
can be wrong; fixed prompting and strict parsing do not guarantee resistance
to instructions embedded in images. Unsupported brands remain unknown.

Same-URL changes without a newly visible password field may need a toolbar
retry; service-worker restarts reset deduplication. A hostile page can remove
the in-page notice; the browser-owned badge is separate. Automated tests simulate
Chrome APIs. The notice was visually checked in isolated headless Chrome;
native installation, permissions and capture/navigation still require the
manual demo check above. This is a local prototype, not general browser protection.
See [plan.md](plan.md) for scope.
