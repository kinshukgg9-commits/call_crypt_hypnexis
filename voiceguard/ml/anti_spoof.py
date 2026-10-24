"""Voice authenticity (anti-spoofing) scoring.

Backends (ANTISPOOF_BACKEND):
  onnx         An ONNX model run with onnxruntime, so the server needs no PyTorch. This is the
               default. ANTISPOOF_ONNX picks the file:
                 ml/models/antideepfake-w2v-small.onnx  (default) AntiDeepfake wav2vec 2.0 Base
                     from NII, CC BY-NC-SA 4.0 (non-commercial); built by scripts/build_voice_model.py.
                     Trained on modern synthetic speech; takes audio of any length.
                 ml/models/aasist.onnx  AASIST (MIT), trained on the 2019 ASVspoof benchmark.
                     Much weaker on today's voices; kept for uses the other licence doesn't allow.
  aasist       AASIST from github.com/clovaai/aasist, run with PyTorch. Fetch the model code,
               config and checkpoint with: python scripts/fetch_aasist.py
  torchscript  Any TorchScript model taking a (1, samples) float waveform and returning
               2-class logits (or a tuple whose last item is the logits).
  none         Voice scoring disabled; results report voice_status "unavailable".

The score is the softmax probability of the "spoof" class. It is not calibrated:
present it as an indicator, never as proof. Label order differs between
checkpoints, so ANTISPOOF_SPOOF_INDEX must match yours (both bundled models: 0 = spoof).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from ml.lazy import LazyModel

log = logging.getLogger("voiceguard.anti_spoof")

DEFAULT_INPUT_SAMPLES = 64000  # 4 s at 16 kHz: the frame the voice is judged on
MIN_INPUT_SAMPLES = 16000      # shorter audio is repeat-padded up to this
AASIST_INPUT_SAMPLES = 64600   # AASIST takes exactly this much


class AntiSpoof:
    def __init__(self, model: Any, spoof_index: int = 0, input_samples: int = AASIST_INPUT_SAMPLES):
        self.model = model
        self.spoof_index = spoof_index
        self.input_samples = input_samples

    def score(self, w: np.ndarray) -> float:
        import torch

        x = np.asarray(w, dtype=np.float32)
        if x.size == 0:
            raise ValueError("cannot score empty audio")
        # Repeat-pad or crop to the model's input length, as the AASIST data loader does.
        x = np.resize(x, self.input_samples).astype(np.float32, copy=False)
        with torch.inference_mode():
            out = self.model(torch.from_numpy(x)[None])
        logits = out[-1] if isinstance(out, (tuple, list)) else out   # AASIST returns (hidden, logits)
        p = float(torch.softmax(logits.float(), dim=-1)[0, self.spoof_index])
        if not math.isfinite(p):
            raise ValueError("model returned a non-finite score")
        return min(max(p, 0.0), 1.0)


class OnnxAntiSpoof:
    """Same interface as AntiSpoof, running an ONNX model with onnxruntime (no PyTorch).

    A model with a fixed input length (AASIST) gets its audio repeat-padded or cropped to
    that length. A model that takes any length (AntiDeepfake) gets the audio as it is, cropped
    to the most recent `input_samples`.
    """

    def __init__(self, path: Path, spoof_index: int = 0, input_samples: int = 0, threads: int = 0):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads or min(4, os.cpu_count() or 1)
        options.inter_op_num_threads = 1
        # Without this, onnxruntime keeps ~300 MB of scratch memory between runs (measured);
        # releasing it after each run costs no speed and keeps the server small.
        options.enable_cpu_mem_arena = False
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        model_input = self.session.get_inputs()[0]
        self.input_name = model_input.name
        fixed = model_input.shape[-1]
        self.fixed_length = isinstance(fixed, int)
        self.input_samples = input_samples or (fixed if self.fixed_length else DEFAULT_INPUT_SAMPLES)
        self.spoof_index = spoof_index

    def score(self, w: np.ndarray) -> float:
        x = np.asarray(w, dtype=np.float32)
        if x.size == 0:
            raise ValueError("cannot score empty audio")
        if self.fixed_length:
            x = np.resize(x, self.input_samples)
        else:
            x = x[-self.input_samples:]
            if x.size < MIN_INPUT_SAMPLES:
                x = np.resize(x, MIN_INPUT_SAMPLES)
        x = np.ascontiguousarray(x, dtype=np.float32)
        logits = np.asarray(self.session.run(None, {self.input_name: x[None]})[0], dtype=np.float64)[0]
        e = np.exp(logits - logits.max())                      # softmax
        p = float(e[self.spoof_index] / e.sum())
        if not math.isfinite(p):
            raise ValueError("model returned a non-finite score")
        return min(max(p, 0.0), 1.0)


def load_onnx(path: Path, spoof_index: int = 0, input_samples: int = 0, threads: int = 0) -> OnnxAntiSpoof:
    _require(path, "Create it with: python scripts/build_voice_model.py (or point ANTISPOOF_ONNX at another model).")
    return OnnxAntiSpoof(path, spoof_index, input_samples, threads)


def _require(path: Path, hint: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. {hint}")


def load_aasist(model_py: Path, config: Path, ckpt: Path, spoof_index: int = 0, input_samples: int = 0) -> AntiSpoof:
    hint = "Run: python scripts/fetch_aasist.py"
    for path in (model_py, config, ckpt):
        _require(path, hint)
    import torch
    spec = importlib.util.spec_from_file_location("voiceguard_aasist_model", model_py)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import model code from {model_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg = json.loads(config.read_text(encoding="utf-8"))
    model_cfg = cfg.get("model_config", cfg)
    model = module.Model(model_cfg)
    model.load_state_dict(torch.load(ckpt, map_location="cpu", weights_only=True))
    model.eval()
    n = input_samples or int(model_cfg.get("nb_samp", AASIST_INPUT_SAMPLES))
    return AntiSpoof(model, spoof_index, n)


def load_torchscript(ckpt: Path, spoof_index: int = 0, input_samples: int = 0) -> AntiSpoof:
    _require(ckpt, "Set ANTISPOOF_CKPT to your exported TorchScript file.")
    import torch
    model = torch.jit.load(str(ckpt), map_location="cpu")
    model.eval()
    return AntiSpoof(model, spoof_index, input_samples or AASIST_INPUT_SAMPLES)


class VoiceDetector:
    """Lazily loaded detector used by the analysis pipeline. score() returns None when unavailable."""

    def __init__(self, settings: Any):
        self.settings = settings
        backend = settings.antispoof_backend
        if backend == "onnx":
            loader = self._load_onnx
            detail = f"onnx ({Path(settings.antispoof_onnx).name})"
        elif backend == "aasist":
            loader = self._load_aasist
            detail = f"aasist ({Path(settings.antispoof_ckpt).name})"
        elif backend == "torchscript":
            loader = self._load_torchscript
            detail = f"torchscript ({Path(settings.antispoof_ckpt).name})"
        else:
            loader, detail = None, "disabled (ANTISPOOF_BACKEND=none)"
        self._slot: LazyModel[Any] = LazyModel("anti-spoof model", loader, detail)

    def _configure_torch(self) -> None:
        import torch

        # A small model: a few threads beat torch's one-per-core default (less sync overhead).
        torch.set_num_threads(self.settings.torch_num_threads or min(4, os.cpu_count() or 1))

    def _load_onnx(self) -> OnnxAntiSpoof:
        s = self.settings
        return load_onnx(
            Path(s.antispoof_onnx), s.antispoof_spoof_index, s.antispoof_input_samples, s.torch_num_threads
        )

    def _load_aasist(self) -> AntiSpoof:
        s = self.settings
        model = load_aasist(
            Path(s.antispoof_model_py), Path(s.antispoof_config), Path(s.antispoof_ckpt),
            s.antispoof_spoof_index, s.antispoof_input_samples,
        )
        self._configure_torch()             # after loading, so missing weights report the fix, not a missing torch
        return model

    def _load_torchscript(self) -> AntiSpoof:
        s = self.settings
        model = load_torchscript(Path(s.antispoof_ckpt), s.antispoof_spoof_index, s.antispoof_input_samples)
        self._configure_torch()
        return model

    def preload(self) -> None:
        self._slot.get()

    def status(self) -> dict:
        return self._slot.status()

    def score(self, w: np.ndarray) -> float | None:
        det = self._slot.get()
        if det is None:
            return None
        try:
            return det.score(w)
        except Exception:
            log.exception("Voice scoring failed for one window")
            return None


_det: VoiceDetector | None = None


def get_detector() -> VoiceDetector:
    """Process-wide detector configured from environment variables (handy for scripts)."""
    global _det
    if _det is None:
        from backend.config import Settings

        _det = VoiceDetector(Settings.from_env())
    return _det
