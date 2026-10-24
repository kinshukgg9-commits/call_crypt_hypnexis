"""Speech-to-text with faster-whisper (CPU-friendly; handles Hindi and English).

The model loads lazily (or in the background at startup), so importing this module
is cheap and the API is up before the model is.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from ml.lazy import LazyModel

log = logging.getLogger("voiceguard.transcription")


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None = None
    language_probability: float = 0.0


class WhisperTranscriber:
    """transcribe() returns a Transcript, or None when speech-to-text is unavailable."""

    def __init__(self, settings: Any):
        self.settings = settings
        s = settings
        detail = f"faster-whisper {s.whisper_model} ({s.whisper_device}/{s.whisper_compute_type})"
        loader = self._load if s.transcription_enabled else None
        if loader is None:
            detail = "disabled (TRANSCRIPTION_ENABLED=false)"
        self._slot: LazyModel[Any] = LazyModel("speech-to-text model", loader, detail)

    def _load(self) -> Any:
        from faster_whisper import WhisperModel

        s = self.settings
        return WhisperModel(
            s.whisper_model,
            device=s.whisper_device,
            compute_type=s.whisper_compute_type,
            cpu_threads=s.whisper_cpu_threads,
            download_root=s.whisper_download_root,
        )

    def preload(self) -> None:
        self._slot.get()

    def status(self) -> dict:
        return self._slot.status()

    def transcribe(self, w: np.ndarray, language: str | None = None) -> Transcript | None:
        """`language` is a hint (e.g. the session's detected language); WHISPER_LANG overrides it.
        With neither, Whisper detects the language, which costs a second encoder pass."""
        model = self._slot.get()
        if model is None:
            return None
        if len(w) == 0:
            return Transcript("")
        try:
            segs, info = model.transcribe(
                np.ascontiguousarray(w, dtype=np.float32),
                language=self.settings.whisper_lang or language,
                beam_size=self.settings.whisper_beam_size,
                vad_filter=True,                  # vad_filter cuts silence hallucinations
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            text = " ".join(s.text.strip() for s in segs).strip()
        except Exception:
            log.exception("Transcription failed for one window")
            return None
        if not text:
            return Transcript("")
        return Transcript(text, getattr(info, "language", None), float(getattr(info, "language_probability", 0.0) or 0.0))


_default: WhisperTranscriber | None = None


def transcribe(w: np.ndarray) -> str:
    """Convenience wrapper configured from environment variables (handy for scripts)."""
    global _default
    if _default is None:
        from backend.config import Settings

        _default = WhisperTranscriber(Settings.from_env())
    result = _default.transcribe(w)
    return result.text if result else ""
