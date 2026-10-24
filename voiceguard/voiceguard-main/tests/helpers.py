"""Fake models and audio helpers shared by the tests."""
from __future__ import annotations

import io
import time
import wave

import numpy as np

from backend.transcription import Transcript

SR = 16000
FRAME_BYTES = 4096  # 2048 PCM16 samples: what the browser worklet sends (~128 ms)


class FakeTranscriber:
    """Returns scripted texts, one per window (then empty strings)."""

    def __init__(self, texts=(), available=True, error=None, language="en"):
        self.texts = list(texts)
        self.available = available
        self.error = error
        self.language = language
        self.calls = 0
        self.hints = []

    def transcribe(self, w, language=None):
        self.calls += 1
        self.hints.append(language)
        if self.error:
            raise self.error
        if not self.available:
            return None
        text = self.texts.pop(0) if self.texts else ""
        if not text:
            return Transcript("")
        return Transcript(text, language or self.language, 1.0 if language else 0.9)

    def preload(self):
        pass

    def status(self):
        if self.available:
            return {"state": "ready", "detail": "fake transcriber", "error": None}
        return {"state": "error", "detail": "fake transcriber", "error": "RuntimeError: offline"}


class FakeDetector:
    def __init__(self, value=0.1, delay=0.0):
        self.value = value
        self.delay = delay
        self.calls = 0
        self.lengths = []

    def score(self, w):
        self.calls += 1
        self.lengths.append(len(w))
        if self.delay:
            time.sleep(self.delay)
        return self.value

    def preload(self):
        pass

    def status(self):
        state = "ready" if self.value is not None else "error"
        return {"state": state, "detail": "fake detector", "error": None}


def tone(seconds: float, sr: int = SR, amp: float = 0.3) -> np.ndarray:
    """A loud-enough, unclipped signal that passes the quality check."""
    t = np.arange(int(round(seconds * sr))) / sr
    return (amp * np.sin(2 * np.pi * 220 * t) + 0.05 * np.sin(2 * np.pi * 1330 * t)).astype(np.float32)


def pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def wav_bytes(x: np.ndarray, sr: int = SR, channels: int = 1) -> bytes:
    data = np.repeat(x[:, None], channels, axis=1) if channels > 1 else x
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16(data.reshape(-1)))
    return buf.getvalue()


def send_audio(ws, x: np.ndarray, frame_bytes: int = FRAME_BYTES) -> None:
    data = pcm16(x)
    for i in range(0, len(data), frame_bytes):
        ws.send_bytes(data[i:i + frame_bytes])
