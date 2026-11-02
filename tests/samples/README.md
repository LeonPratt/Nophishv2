# OCR fixtures

These are published documentation screenshots, downloaded unchanged for M2.
They contain public example forms, not captured user sessions. The M2 login
images include side-by-side/annotated examples; this is a smoke test, not a
representative phishing dataset. Runtime screenshots are never saved.

| File | Expected gate | Source and attribution |
| --- | --- | --- |
| microsoft-login.png | pass | Microsoft, [default background comparison](https://learn.microsoft.com/en-us/entra/fundamentals/media/how-to-customize-branding/background-image-update.png), from [company branding documentation](https://learn.microsoft.com/en-us/entra/fundamentals/how-to-customize-branding). |
| branded-login.png | pass | Microsoft, [annotated sign-in page](https://learn.microsoft.com/en-us/entra/fundamentals/media/how-to-customize-branding/sign-in-page-map.png), same documentation. |
| hello-extension.png | reject | Google, [Hello Extension popup](https://developer.chrome.com/static/docs/extensions/get-started/tutorial/hello-world/image/hello-extension-6e3eacba176d3.png), from [Hello World tutorial](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world). |
| extensions-page.png | reject | Google, [extensions page](https://developer.chrome.com/static/docs/extensions/get-started/tutorial/hello-world/image/extensions-page-e0d64d89a6acf.png), same tutorial. |
| google-login.png | pass; google | Microsoft, [Google sign-in screenshot](https://learn.microsoft.com/en-us/entra/external-id/media/google-federation/google-sign-in.png), from [Google federation documentation](https://learn.microsoft.com/en-us/entra/external-id/google-federation). Includes only the literal placeholder `<user>@gmail.com`. |
| facebook-login.png | pass; meta | Microsoft, [Facebook login screenshot](https://learn.microsoft.com/en-us/aspnet/web-pages/overview/security/enabling-login-from-external-sites-in-an-aspnet-web-pages-site/_static/image4.png), from [external login documentation](https://learn.microsoft.com/en-us/aspnet/web-pages/overview/security/enabling-login-from-external-sites-in-an-aspnet-web-pages-site). |

Microsoft documentation: [repository license](https://github.com/MicrosoftDocs/entra-docs/blob/main/LICENSE), CC BY 4.0; trademarks remain with their owners.
Google documentation: [site policies](https://developers.google.com/terms/site-policies), CC BY 4.0 except where noted; trademarks remain with their owners.

Run `python -m server.ocr --check-samples` from the repository root after CUDA
dependencies and model weights are installed. Only filenames, gate results,
device information, and timings are printed; extracted text is not printed.

For M3 run `python -m server.classifier --check-samples`: this uses the actual
FastAPI pipeline, both CUDA models, and a spy around the real VLM function to
assert zero calls for rejected images. `cases.json` records expected brands:
Microsoft for the background comparison and unknown for unsupported Woodgrove
branding (despite its secondary Google provider button).

M4 also checks each known-brand image with the explicit `approved_url` from
`cases.json` (expect `safe`) and an unapproved test hostname (expect `dangerous`).
No request is sent to those external hosts: the URL is just data in a local
TestClient request. There are nine endpoint scenarios across six images.

## M5 evaluation probes

`evaluation.json` adds five synthetic screenshots rendered by `create_probes.py`
with Pillow (Windows Arial, 1000 × 700, empty fields). They are test drawings,
not captured user sessions or genuine provider screenshots:

| File | Ground truth / purpose |
| --- | --- |
| probe-unrelated.png | Northwind Library login; gate pass, unknown brand |
| probe-french.png | French library login; expected pass, tests English-rule limitation |
| probe-passwordless.png | Email-only account access with Continue; expected pass |
| probe-article.png | Garden article mentioning email; expected reject |
| probe-login-link.png | Garden article with Log in navigation; expected reject |

Regenerate these public fixtures with `python tests/samples/create_probes.py`.
Run `python tests/evaluate.py` for three rounds through the real CUDA endpoint.
The six original screenshots are development smoke tests; their prompt was
tuned before M5. The five new probes were evaluated without changing the
production prompt or gate. Three known-brand images are replayed with approved
and unapproved URL metadata to represent exact visual copies. No live phishing
sites, credential submissions or external requests are involved. The report
retains missed logins and false gate passes, storing labels and timings only,
never model output text or OCR text.

After the Ollama migration, these same commands use local `qwen3-vl:2b` via
Ollama instead of Transformers. Install the model separately and start Ollama
before running them. The committed `../evaluation-results.json` is the historical
Transformers baseline until evaluation is re-run; it is not an Ollama result.
