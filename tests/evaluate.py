"""Small offline CUDA evaluation. Run from the repo root: python tests/evaluate.py."""

import base64
from datetime import datetime, timezone
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
from statistics import median
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch
from fastapi.testclient import TestClient
from server.main import app
from server.ocr import check_login
from server.classifier import classify_brand, MODEL_ID


def latency(values):
    values = sorted(values)
    return {"n": len(values), "median_ms": round(median(values), 1),
            "p95_ms": round(values[math.ceil(len(values) * .95) - 1], 1)} if values else None


def main():
    samples = ROOT / "tests" / "samples"
    cases = json.loads((samples / "cases.json").read_text(encoding="utf-8"))
    cases += json.loads((samples / "evaluation.json").read_text(encoding="utf-8"))
    records = []
    stages = {}

    def ocr(*args):
        detected, elapsed = check_login(*args)
        stages.update(gate=detected, ocr_ms=elapsed)
        return detected, elapsed

    def vlm(*args):
        brand, elapsed = classify_brand(*args)
        stages["vlm_calls"] += 1
        stages["vlm_ms"] = elapsed
        return brand, elapsed

    with patch("server.main.check_login", side_effect=ocr), patch("server.main.classify_brand", side_effect=vlm):
        with TestClient(app) as client:
            if app.state.ocr_reader is None or app.state.classifier is None:
                raise RuntimeError("Both CUDA models must load; evaluation cannot use mocks or CPU fallback")
            environment = {"gpu": torch.cuda.get_device_name(0), "python": platform.python_version(),
                           "torch": torch.__version__, "easyocr": version("easyocr"),
                           "vlm_backend": "ollama", "vlm_model": MODEL_ID}
            for round_number in range(3):
                for case in cases:
                    image = (samples / case["file"]).read_bytes()
                    screenshot = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
                    known = case.get("brand") in {"microsoft", "google", "meta"}
                    expected = "not_login" if not case["is_login"] else "dangerous" if known else "unknown"
                    scenarios = [("unapproved", "https://fixture.invalid/", expected)]
                    if case.get("approved_url"):
                        scenarios.append(("approved", case["approved_url"], "safe"))
                    for scenario, url, verdict in scenarios:
                        stages.clear()
                        stages["vlm_calls"] = 0
                        start = perf_counter()
                        response = client.post("/analyze", json={"url": url, "screenshot": screenshot})
                        total = round((perf_counter() - start) * 1000, 1)
                        if response.status_code != 200 or "gate" not in stages:
                            raise RuntimeError("Evaluation endpoint or OCR failed")
                        result = response.json()
                        if stages["vlm_calls"] != int(stages["gate"]):
                            raise RuntimeError("OCR gate/VLM call-count invariant failed")
                        record = {"file": case["file"], "cohort": case.get("cohort", "development_smoke"),
                                  "round": round_number, "scenario": scenario,
                                  "expected_gate": case["is_login"], "expected_brand": case.get("brand"),
                                  "expected_verdict": verdict, **stages, "brand": result["brand"],
                                  "verdict": result["verdict"], "endpoint_ms": total}
                        records.append(record)
                        print(f"{case['file']} {scenario} round={round_number}: gate={stages['gate']} "
                              f"brand={result['brand']} verdict={result['verdict']} total_ms={total}", flush=True)

    # Accuracy uses one observation per image/scenario, not repeated timings as independent samples.
    first = [r for r in records if r["round"] == 0]
    images = [r for r in first if r["scenario"] == "unapproved"]
    logins = [r for r in images if r["expected_gate"]]
    ordinary = [r for r in images if not r["expected_gate"]]
    passing = [r for r in logins if r["gate"]]
    benign = [r for r in first if r["expected_verdict"] != "dangerous"]
    warm = [r for r in records if r["round"] > 0]
    summary = {
        "unique_images": len(images), "unique_scenarios": len(first),
        "login_gate": {"passed": sum(r["gate"] for r in logins), "total": len(logins)},
        "ordinary_gate": {"false_passes": sum(r["gate"] for r in ordinary), "total": len(ordinary)},
        "brand_on_gate_passing_logins": {"correct": sum(r["brand"] == r["expected_brand"] for r in passing), "total": len(passing)},
        "false_warnings": {"count": sum(r["verdict"] == "dangerous" for r in benign), "benign_scenarios": len(benign)},
        "verdicts": {"correct": sum(r["verdict"] == r["expected_verdict"] for r in first), "total": len(first)},
        "rejected_requests_with_vlm_calls": sum(not r["gate"] and r["vlm_calls"] > 0 for r in records),
        "warm_latency": {"ocr_gpu_stage": latency([r["ocr_ms"] for r in warm]),
                         "vlm_gpu_stage": latency([r["vlm_ms"] for r in warm if r["vlm_calls"]]),
                         "endpoint_login_path": latency([r["endpoint_ms"] for r in warm if r["gate"]]),
                         "endpoint_rejected_path": latency([r["endpoint_ms"] for r in warm if not r["gate"]])},
        "repeat_label_changes": sum((r["gate"], r["brand"], r["verdict"]) !=
                                    (base["gate"], base["brand"], base["verdict"])
                                    for r in records if r["round"] > 0 for base in first
                                    if (r["file"], r["scenario"]) == (base["file"], base["scenario"])),
    }
    report = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "environment": environment, "method": "Three sequential TestClient rounds; first round excluded from latency. "
              "OCR uses CUDA-synchronized wall time. VLM uses Ollama HTTP wall time, including preprocessing "
              "and GPU-placement verification, not kernel-only time. "
              "Endpoint includes JSON serialization/validation/models/domain check; excludes startup, browser capture, "
              "debounce and socket transport. Approved/unapproved copies use identical public bytes; no external requests. "
              "Development smoke fixtures previously informed the prompt; synthetic probes are not real phishing pages.",
              "summary": summary, "records": records}
    (ROOT / "tests" / "evaluation-results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
