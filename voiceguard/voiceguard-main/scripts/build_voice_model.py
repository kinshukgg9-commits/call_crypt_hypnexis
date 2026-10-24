"""Build the voice model the server ships with: ml/models/antideepfake-w2v-small.onnx (+ .w* weights).

The model is "Wav2Vec2-Small-AntiDeepfake" from the Yamagishi lab at NII:
https://huggingface.co/nii-yamagishilab/wav2vec-small-anti-deepfake (CC BY-NC-SA 4.0, see
ml/models/LICENSE-ANTIDEEPFAKE). It is wav2vec 2.0 Base, post-trained on about 74,000 hours of
real and synthetic speech, with a mean-pool + linear head.

The files in the repository were built from weights fine-tuned for this project (ml/train/):

    pip install -r requirements-dev.txt
    python scripts/build_voice_model.py --weights data/voice/run/blend50.pt

Without --weights it builds the public checkpoint as it is.

What it does:
  1. downloads the checkpoint (380 MB, hash-checked) into ml/weights/
  2. rebuilds the network in plain PyTorch (below; fairseq parameter names, so no fairseq needed)
  3. exports it to ONNX with the input normalisation built in
  4. stores the transformer weights as 8-bit integers (378 MB -> 122 MB). The convolutions stay
     32-bit: quantizing them too makes the detector flag real voices (measured).
  5. splits the weights into files under 50 MB, so the repository needs no Git LFS
  6. checks the result against PyTorch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
REVISION = "9a13264b5dcc827a8d5a4f8e01fccefa392f886b"
URL = f"https://huggingface.co/nii-yamagishilab/wav2vec-small-anti-deepfake/resolve/{REVISION}/model.safetensors"
SHA256 = "828ee456122f86d5d631cb7895a10e5c62c78a4fcb8a8b1c42cb5838a9abcfe0"
CKPT = ROOT / "ml" / "weights" / "antideepfake-w2v-small.safetensors"
OUT = ROOT / "ml" / "models" / "antideepfake-w2v-small.onnx"
PART_BYTES = 45 * 1024 * 1024

CONV = [(10, 5)] + [(3, 2)] * 4 + [(2, 2)] * 2      # (kernel, stride); 512 channels each
DIM, HEADS, LAYERS, FF = 768, 12, 12, 3072


def build_detector():
    """wav2vec 2.0 Base + mean-pool + linear head. Input (batch, samples) of 16 kHz audio;
    output logits (batch, 2) with index 0 = fake, 1 = real."""
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class Attention(nn.Module):
        def __init__(self):
            super().__init__()
            self.k_proj, self.v_proj = nn.Linear(DIM, DIM), nn.Linear(DIM, DIM)
            self.q_proj, self.out_proj = nn.Linear(DIM, DIM), nn.Linear(DIM, DIM)

        def forward(self, x):                                    # (batch, frames, DIM)
            b, t, d = x.shape

            def heads(y):
                return y.view(b, t, HEADS, d // HEADS).transpose(1, 2)

            q = heads(self.q_proj(x) * (d // HEADS) ** -0.5)
            attn = torch.softmax(q @ heads(self.k_proj(x)).transpose(-1, -2), dim=-1)
            return self.out_proj((attn @ heads(self.v_proj(x))).transpose(1, 2).reshape(b, t, d))

    class Layer(nn.Module):                                      # post-norm transformer layer
        def __init__(self):
            super().__init__()
            self.self_attn = Attention()
            self.self_attn_layer_norm = nn.LayerNorm(DIM)
            self.fc1, self.fc2 = nn.Linear(DIM, FF), nn.Linear(FF, DIM)
            self.final_layer_norm = nn.LayerNorm(DIM)

        def forward(self, x):
            x = self.self_attn_layer_norm(x + self.self_attn(x))
            return self.final_layer_norm(x + self.fc2(F.gelu(self.fc1(x))))

    class Encoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.pos_conv = nn.Sequential(nn.Conv1d(DIM, DIM, 128, padding=64, groups=16))
            self.layers = nn.ModuleList(Layer() for _ in range(LAYERS))
            self.layer_norm = nn.LayerNorm(DIM)

        def forward(self, x):
            pos = F.gelu(self.pos_conv(x.transpose(1, 2))[:, :, :-1]).transpose(1, 2)
            x = self.layer_norm(x + pos)
            for layer in self.layers:
                x = layer(x)
            return x

    class FeatureExtractor(nn.Module):
        def __init__(self):
            super().__init__()
            blocks, channels = [], 1
            for i, (kernel, stride) in enumerate(CONV):
                norm = nn.GroupNorm(512, 512) if i == 0 else nn.Identity()
                blocks.append(nn.Sequential(
                    nn.Conv1d(channels, 512, kernel, stride=stride, bias=False), nn.Identity(), norm, nn.GELU()))
                channels = 512
            self.conv_layers = nn.ModuleList(blocks)

        def forward(self, x):                                    # (batch, 1, samples)
            for block in self.conv_layers:
                x = block(x)
            return x

    class Wav2Vec2(nn.Module):
        def __init__(self):
            super().__init__()
            self.feature_extractor = FeatureExtractor()
            self.layer_norm = nn.LayerNorm(512)
            self.post_extract_proj = nn.Linear(512, DIM)
            self.encoder = Encoder()

        def forward(self, wav):
            x = self.feature_extractor(wav[:, None]).transpose(1, 2)
            return self.encoder(self.post_extract_proj(self.layer_norm(x)))

    class Detector(nn.Module):
        def __init__(self):
            super().__init__()
            self.model = Wav2Vec2()
            self.proj_fc = nn.Linear(DIM, 2)

        def forward(self, wav):
            mean = wav.mean(-1, keepdim=True)                    # the model expects zero-mean,
            var = ((wav - mean) ** 2).mean(-1, keepdim=True)     # unit-variance audio
            wav = (wav - mean) / torch.sqrt(var + 1e-5)
            return self.proj_fc(self.model(wav).mean(1))

    return Detector()


def read_safetensors(path: Path) -> dict:
    import torch

    with open(path, "rb") as f:
        header = json.loads(f.read(struct.unpack("<Q", f.read(8))[0]))
        data = f.read()
    header.pop("__metadata__", None)
    tensors = {}
    for name, info in header.items():
        if info["dtype"] != "F32":
            raise ValueError(f"unexpected dtype {info['dtype']} for {name}")
        start, end = info["data_offsets"]
        array = np.frombuffer(data, np.float32, (end - start) // 4, start).reshape(info["shape"])
        tensors[name] = torch.from_numpy(array.copy())
    return tensors


def load_detector(path: Path):
    state = {}
    for name, tensor in read_safetensors(path).items():
        name = name.replace("m_ssl.model.", "model.")
        if name.startswith(("model.quantizer", "model.project_q", "model.final_proj", "model.mask_emb")):
            continue                                             # pre-training parts, unused for detection
        state[name] = tensor
    g = state.pop("model.encoder.pos_conv.0.weight_g")           # fold the weight normalisation
    v = state.pop("model.encoder.pos_conv.0.weight_v")
    state["model.encoder.pos_conv.0.weight"] = g * v / v.pow(2).sum((0, 1), keepdim=True).sqrt()
    detector = build_detector()
    detector.load_state_dict(state, strict=True)
    return detector.eval()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(force: bool = False) -> Path:
    if CKPT.is_file() and not force and sha256(CKPT) == SHA256:
        print(f"ok        {CKPT.name}")
        return CKPT
    CKPT.parent.mkdir(parents=True, exist_ok=True)
    print(f"download  {URL}  (380 MB)")
    tmp = CKPT.with_suffix(".part")
    with urllib.request.urlopen(URL, timeout=60) as response, open(tmp, "wb") as f:
        for block in iter(lambda: response.read(1 << 20), b""):
            f.write(block)
    if sha256(tmp) != SHA256:
        tmp.unlink()
        raise SystemExit("Downloaded file does not match the expected SHA-256; not using it.")
    tmp.replace(CKPT)
    return CKPT


def split_weights(src: Path, dst: Path) -> list[Path]:
    """Save `src` as `dst` with its weights in dst.w0, dst.w1, ... (each under PART_BYTES)."""
    import onnx
    from onnx.external_data_helper import set_external_data

    model = onnx.load(str(src))
    for old in dst.parent.glob(dst.stem + ".w*"):
        old.unlink()
    parts: list[Path] = []
    handle, used = None, 0
    for tensor in model.graph.initializer:
        if not tensor.HasField("raw_data") or len(tensor.raw_data) < 1024:
            continue                                             # small tensors stay inside the .onnx
        raw = tensor.raw_data
        if handle is None or used + len(raw) > PART_BYTES:
            if handle:
                handle.close()
            parts.append(dst.with_suffix(f".w{len(parts)}"))
            handle, used = open(parts[-1], "wb"), 0
        handle.write(raw)
        set_external_data(tensor, location=parts[-1].name, offset=used, length=len(raw))
        tensor.ClearField("raw_data")
        used += len(raw)
    if handle:
        handle.close()
    onnx.save(model, str(dst))
    return parts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the ONNX voice model from the AntiDeepfake checkpoint.")
    ap.add_argument("--force", action="store_true", help="download the checkpoint again")
    ap.add_argument("--weights", type=Path, help="fine-tuned weights from ml/train/train.py (default: the public checkpoint)")
    args = ap.parse_args(argv)

    import onnxruntime as ort
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic

    if args.weights:
        detector = build_detector()
        detector.load_state_dict(torch.load(args.weights, map_location="cpu"))
        detector.eval()
    else:
        detector = load_detector(fetch(args.force))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        full, small = Path(tmp) / "fp32.onnx", Path(tmp) / "int8.onnx"
        torch.onnx.export(
            detector, (torch.zeros(1, 64000),), str(full), input_names=["audio"], output_names=["logits"],
            dynamic_axes={"audio": {0: "batch", 1: "samples"}, "logits": {0: "batch"}},
            opset_version=17, dynamo=False,
        )
        quantize_dynamic(str(full), str(small), weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul"])
        parts = split_weights(small, OUT)

        def session(path: Path):
            return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])

        rng = np.random.default_rng(0)
        t = np.arange(64000) / 16000
        cases = {
            "noise": rng.standard_normal(64000).astype(np.float32) * 0.1,
            "chirp": (0.3 * np.sin(2 * np.pi * (100 + 400 * t) * t)).astype(np.float32),
            "3 s of noise": rng.standard_normal(48000).astype(np.float32) * 0.05,
        }
        exact, shipped = session(full), session(OUT)
        worst_export = worst_quant = 0.0
        for name, x in cases.items():
            with torch.inference_mode():
                want = detector(torch.from_numpy(x)[None]).numpy()
            got_full = exact.run(None, {"audio": x[None]})[0]
            got = shipped.run(None, {"audio": x[None]})[0]
            worst_export = max(worst_export, float(np.abs(want - got_full).max()))
            worst_quant = max(worst_quant, float(np.abs(want - got).max()))
            print(f"{name:13} torch={want.ravel().round(3)} onnx={got_full.ravel().round(3)} shipped={got.ravel().round(3)}")
    if worst_export > 1e-3 or worst_quant > 1.5:
        print(f"MISMATCH: export differs by {worst_export:.2e}, 8-bit model by {worst_quant:.2f} (logits).", file=sys.stderr)
        return 1
    files = [OUT, *parts]
    for path in files:
        print(f"wrote {path.relative_to(ROOT)}  {path.stat().st_size / 1e6:6.1f} MB")
    print(f"export error {worst_export:.1e}; 8-bit error {worst_quant:.2f} (logits, on noise). "
          "Now measure it on speech: python -m ml.evaluate --genuine DIR --spoof DIR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
