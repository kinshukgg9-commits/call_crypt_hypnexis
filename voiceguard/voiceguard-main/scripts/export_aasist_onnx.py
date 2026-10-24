"""Convert the pretrained AASIST model to ONNX so the server can run it without PyTorch.

PyTorch needs several hundred MB of memory just to load; onnxruntime (already installed
with faster-whisper) runs the same model in a fraction of that. Needed only once, on a
machine that has the dev requirements:

    pip install onnx onnxscript
    python scripts/fetch_aasist.py
    python scripts/export_aasist_onnx.py          # writes ml/models/aasist.onnx

The script checks that the ONNX model gives the same scores as the PyTorch one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import Settings  # noqa: E402
from ml.anti_spoof import load_aasist  # noqa: E402

OUT = ROOT / "ml" / "models" / "aasist.onnx"


def main() -> int:
    import onnxruntime as ort
    import torch

    s = Settings()
    det = load_aasist(s.antispoof_model_py, s.antispoof_config, s.antispoof_ckpt)
    n = det.input_samples

    class LogitsOnly(torch.nn.Module):          # AASIST returns (hidden, logits)
        def __init__(self, model):
            super().__init__()
            self.model = model

        def forward(self, x):
            return self.model(x)[1]

    wrapped = LogitsOnly(det.model).eval()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    example = torch.zeros(1, n, dtype=torch.float32)
    torch.onnx.export(
        wrapped, (example,), str(OUT), input_names=["audio"], output_names=["logits"],
        opset_version=17, dynamo=False,
    )

    session = ort.InferenceSession(str(OUT), providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(0)
    t = np.arange(n) / 16000
    cases = {
        "noise": rng.standard_normal(n).astype(np.float32) * 0.1,
        "tone": (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32),
        "quiet noise": rng.standard_normal(n).astype(np.float32) * 0.01,
        "chirp": (0.3 * np.sin(2 * np.pi * (100 + 400 * t) * t)).astype(np.float32),
    }
    worst = 0.0
    for name, x in cases.items():
        with torch.inference_mode():
            want = wrapped(torch.from_numpy(x)[None]).numpy()
        got = session.run(None, {"audio": x[None]})[0]
        diff = float(np.abs(want - got).max())
        worst = max(worst, diff)
        print(f"{name:12} torch={want.ravel().round(4)} onnx={got.ravel().round(4)} max diff={diff:.2e}")
    if worst > 1e-3:
        print(f"MISMATCH: ONNX differs from PyTorch by {worst:.2e}; not using this export.", file=sys.stderr)
        OUT.unlink(missing_ok=True)
        return 1
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes); input {n} samples; max diff {worst:.2e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
