"""CUDA OCR gate, local brand recognition, then deterministic hostname checks."""

import asyncio
import base64
import binascii
import io
import json
import logging
import sqlite3
import warnings
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from server.ocr import check_login, load_reader
from server.classifier import classify_brand, load_classifier, normalize_brand
from server.domains import normalize_hostname, verify_domain
from server import policies

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app):
    app.state.ocr_reader = None
    app.state.classifier = None
    app.state.gpu_lock = asyncio.Lock()
    try:
        app.state.ocr_reader = await run_in_threadpool(load_reader)
        logger.info("EasyOCR ready: detector and recognizer on CUDA")
    except Exception as error:
        # Never echo model/input data or silently fall back to CPU.
        logger.error("EasyOCR unavailable (%s). CUDA is required; run python -m server.ocr --download-models. Analysis will return unknown.", type(error).__name__)
    if app.state.ocr_reader is not None:
        try:
            app.state.classifier = await run_in_threadpool(load_classifier)
            logger.info("Ollama ready: local brand model fully GPU-resident")
        except Exception as error:
            logger.error("VLM unavailable (%s). Start local Ollama with qwen3.5:2b installed and check GPU placement; restart this server after setup. Login pages will return unknown.", type(error).__name__)
    yield
    app.state.ocr_reader = None
    app.state.classifier = None


app = FastAPI(title="NoPhish local prototype", lifespan=lifespan)
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_PIXELS = 20_000_000


async def read_json(request, limit=MAX_REQUEST_BYTES):
    # Bound the body before parsing JSON, including requests without Content-Length.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "Request too large")
        body.extend(chunk)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Expected a JSON object") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected a JSON object")
    return data


@app.get("/domain-policies")
async def domain_policies():
    try:
        return {"entries": await run_in_threadpool(policies.entries)}
    except (sqlite3.Error, OSError):
        raise HTTPException(503, "Domain list unavailable") from None


@app.api_route("/domain-policies", methods=["PUT", "DELETE"])
async def change_domain_policy(request: Request):
    # Browser pages cannot issue this custom-header request without a CORS preflight.
    # We do not enable CORS. Extension background/options pages and local CLI clients can.
    origin = request.headers.get("origin", "")
    if (request.headers.get("x-nophish-action") != "domain-policy"
            or request.headers.get("content-type", "").split(";")[0] != "application/json"
            or (origin and not origin.startswith("chrome-extension://"))):
        raise HTTPException(403, "Domain list changes require an extension action")
    data = await read_json(request, 8192)
    try:
        if request.method == "DELETE":
            host = await run_in_threadpool(policies.remove, data.get("domain"))
            return {"hostname": host, "policy": None}
        host = await run_in_threadpool(policies.save, data.get("domain"), data.get("policy"))
        return policies.verdict(host, data["policy"])
    except (ValueError, UnicodeError, TypeError):
        raise HTTPException(400, "Expected a valid domain and whitelist/blacklist decision") from None
    except (sqlite3.Error, OSError):
        raise HTTPException(503, "Domain list unavailable") from None


@app.post("/analyze")
async def analyze(request: Request):
    data = await read_json(request)

    url = data.get("url")
    try:
        host = normalize_hostname(url)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Expected a valid HTTP(S) URL") from None

    # User decisions precede image decoding, model availability checks and all inference.
    try:
        policy = await run_in_threadpool(policies.lookup, host)
    except (sqlite3.Error, OSError):
        logger.warning("Domain list lookup failed; verdict=unknown")
        return {"verdict": "unknown", "brand": None, "reason": "Domain list unavailable; analysis inconclusive"}
    if policy:
        logger.info("Domain list hit: policy=%s; model analysis skipped", policy)
        return policies.verdict(host, policy)

    screenshot = data.get("screenshot")
    if not isinstance(screenshot, str):
        raise HTTPException(400, "Expected a PNG or JPEG screenshot data URL")
    header, separator, encoded = screenshot.partition(",")
    formats = {"data:image/png;base64": "PNG", "data:image/jpeg;base64": "JPEG"}
    if not separator or header not in formats:
        raise HTTPException(400, "Expected a PNG or JPEG screenshot data URL")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(400, "Invalid screenshot encoding") from None
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Screenshot too large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as image:
                if image.format != formats[header] or image.width * image.height > MAX_PIXELS:
                    raise ValueError
                image.verify()
            # Also ensure the compressed pixels decode successfully.
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, "Invalid or oversized screenshot image") from None

    reader = getattr(request.app.state, "ocr_reader", None)
    if reader is None:
        return {"verdict": "unknown", "brand": None, "reason": "GPU OCR unavailable; analysis inconclusive"}
    try:
        async with request.app.state.gpu_lock:
            is_login, elapsed_ms = await run_in_threadpool(check_login, reader, image_bytes)
    except Exception:
        logger.warning("OCR stage failed; verdict=unknown")
        return {"verdict": "unknown", "brand": None, "reason": "OCR analysis failed; analysis inconclusive"}
    logger.info("OCR gate: is_login=%s; ocr_ms=%.1f", is_login, elapsed_ms)
    if not is_login:
        return {"verdict": "not_login", "brand": None, "reason": "No login page detected"}
    classifier = getattr(request.app.state, "classifier", None)
    if classifier is None:
        return {"verdict": "unknown", "brand": "unknown", "reason": "Login detected; GPU brand model unavailable"}
    try:
        async with request.app.state.gpu_lock:
            brand, vlm_ms = await run_in_threadpool(classify_brand, classifier, image_bytes)
        brand = normalize_brand(brand)
    except Exception:
        logger.warning("VLM stage failed; verdict=unknown")
        return {"verdict": "unknown", "brand": "unknown", "reason": "Login detected; brand analysis failed"}
    logger.info("VLM stage: brand=%s; vlm_ms=%.1f", brand, vlm_ms)
    verdict = verify_domain(brand, url)
    logger.info("Domain stage: verdict=%s; brand=%s", verdict, brand)
    return {
        "verdict": verdict,
        "brand": brand,
        "reason": {
            "unknown": "Login detected; brand unknown",
            "safe": f"Detected {brand} on an approved hostname; brand/domain consistency passed",
            "dangerous": f"Possible {brand} impersonation: hostname is not approved for this brand",
        }[verdict],
    }
