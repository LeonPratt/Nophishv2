"""Local CUDA EasyOCR and a small, editable English login-page gate."""

import argparse
import io
import json
import logging
import re
from pathlib import Path
from time import perf_counter

from PIL import Image

MODEL_DIR = Path(__file__).resolve().parents[1] / ".models" / "easyocr"
OCR_MAX_SIZE = (1280, 720)
OCR_BATCH_SIZE = 16
LOGIN_PHRASES = ("sign in", "signin", "log in", "login")
IDENTITY_PHRASES = ("email address", "username", "email", "phone")


def is_login_text(parts):
    text = " ".join(" ".join(parts).lower().split())

    def contains(phrase):
        return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None

    # A lone email/username/password mention is insufficient evidence.
    return any(contains(p) for p in LOGIN_PHRASES) or (
        contains("password") and any(contains(p) for p in IDENTITY_PHRASES)
    )


def load_reader(download=False):
    import torch
    import easyocr

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required: install CUDA-enabled PyTorch and check the NVIDIA driver")
    reader = easyocr.Reader(
        ["en"], gpu=True, model_storage_directory=str(MODEL_DIR),
        user_network_directory=str(MODEL_DIR / "user_network"),
        download_enabled=download, verbose=False,
    )
    if str(reader.device) != "cuda" or any(
        next(model.parameters()).device.type != "cuda"
        for model in (reader.detector, reader.recognizer)
    ):
        raise RuntimeError("EasyOCR did not place both models on CUDA; CPU fallback is disabled")
    return reader


def check_login(reader, image_bytes):
    import numpy as np
    import torch

    torch.cuda.synchronize()
    start = perf_counter()
    with Image.open(io.BytesIO(image_bytes)) as source:
        source_size = source.size
        image = source.convert("RGB")
    # Preserve aspect ratio and small text on already-small images; never upscale.
    image.thumbnail(OCR_MAX_SIZE, Image.Resampling.LANCZOS)
    # EasyOCR expects BGR for ndarray inputs. Avoid PNG re-encoding after resize.
    pixels = np.asarray(image)[:, :, ::-1].copy()
    parts = reader.readtext(pixels, detail=0, paragraph=False, workers=0,
                            batch_size=OCR_BATCH_SIZE)
    detected = is_login_text(parts)
    torch.cuda.synchronize()
    elapsed_ms = round((perf_counter() - start) * 1000, 1)
    logging.getLogger("uvicorn.error").info(
        "OCR image: source=%dx%d; input=%dx%d; batch_size=%d",
        *source_size, *image.size, OCR_BATCH_SIZE,
    )
    return detected, elapsed_ms


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-models", action="store_true", help="Allow one-time model downloads")
    parser.add_argument("--check-samples", action="store_true", help="Run the small GPU screenshot check")
    args = parser.parse_args()
    reader = load_reader(download=args.download_models)
    import torch
    print(f"EasyOCR device={reader.device}; GPU={torch.cuda.get_device_name(0)}")
    print(f"Detector={next(reader.detector.parameters()).device}; recognizer={next(reader.recognizer.parameters()).device}")
    if args.check_samples:
        sample_dir = Path(__file__).resolve().parents[1] / "tests" / "samples"
        cases = json.loads((sample_dir / "cases.json").read_text(encoding="utf-8"))
        failures = 0
        for case in cases:
            detected, elapsed = check_login(reader, (sample_dir / case["file"]).read_bytes())
            passed = detected == case["is_login"]
            failures += not passed
            print(f"{'PASS' if passed else 'FAIL'} {case['file']}: is_login={detected}; ocr_ms={elapsed}")
        raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
