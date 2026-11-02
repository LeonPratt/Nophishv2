"""Compare OCR sizing/batching on public fixtures only; python tests/benchmark_ocr.py."""

import io
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image
import torch
from server.ocr import load_reader, is_login_text
from evaluate import latency


def main():
    folder = ROOT / "tests" / "samples"
    cases = json.loads((folder / "cases.json").read_text())
    cases += json.loads((folder / "evaluation.json").read_text())
    images = [(c["file"], (folder / c["file"]).read_bytes()) for c in cases]
    # Simulate a HiDPI capture using an explicitly synthetic empty-field fixture.
    source = Image.open(folder / "probe-unrelated.png").convert("RGB")
    canvas = Image.new("RGB", (2560, 1440), "#f4f6f8")
    canvas.paste(source.resize((2000, 1400)), (280, 20))
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    images.append(("synthetic-hidpi-2560x1440", output.getvalue()))
    reader = load_reader()
    records = []
    for repeat in range(3):
        for name, image_bytes in images:
            # Rotate order to reduce systematic warm-up/order bias.
            variants = [("original", None, 1), ("720p", (1280, 720), 1),
                        ("720p-batch16", (1280, 720), 16)]
            variants = variants[repeat:] + variants[:repeat]
            for label, cap, batch in variants:
                torch.cuda.synchronize()
                start = perf_counter()
                image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                original_size = image.size
                if cap:
                    image.thumbnail(cap, Image.Resampling.LANCZOS)
                    data = np.asarray(image)[:, :, ::-1].copy()  # EasyOCR ndarray input is BGR.
                else:
                    data = image_bytes
                parts = reader.readtext(data, detail=0, paragraph=False, workers=0, batch_size=batch)
                gate = is_login_text(parts)
                torch.cuda.synchronize()
                elapsed = round((perf_counter() - start) * 1000, 1)
                records.append(dict(file=name, round=repeat, variant=label,
                                    source_size=original_size, input_size=image.size, gate=gate, ms=elapsed))
                print(f"{name} {label} round={repeat}: size={image.size} gate={gate} ms={elapsed}", flush=True)
    summary = {}
    for label in ["original", "720p", "720p-batch16"]:
        rows = [r for r in records if r["variant"] == label and r["round"] > 0]
        changes = sum(r["gate"] != base["gate"] for r in rows for base in records
                      if base["variant"] == "original" and (base["file"], base["round"]) == (r["file"], r["round"]))
        summary[label] = {**latency([r["ms"] for r in rows]), "gate_changes_vs_original": changes}
    result = dict(gpu=torch.cuda.get_device_name(0), torch=torch.__version__,
                  method="CUDA-synchronized OCR wall time; three rounds; first excluded from summary; "
                         "public/synthetic fixtures only; HiDPI is a synthetic layout, not a user capture.",
                  summary=summary, records=records)
    (ROOT / "tests" / "ocr-performance.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
