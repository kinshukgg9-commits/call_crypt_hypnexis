"""Speech-to-text with faster-whisper (CPU-friendly; handles Hindi and English).

The model loads lazily (or in the background at startup), so importing this module
is cheap and the API is up before the model is.
"""
from __future__ import annotations

import bisect
import logging
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

from ml.lazy import LazyModel

log = logging.getLogger("voiceguard.transcription")


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None = None
    language_probability: float = 0.0


def split_words(words: Iterable[Any], ends: Sequence[float]) -> list[str]:
    """Texts of the windows ending at `ends` (seconds): each word goes where its middle falls."""
    parts: list[list[str]] = [[] for _ in ends]
    for w in words:
        i = min(bisect.bisect_left(ends, (w.start + w.end) / 2), len(ends) - 1)
        parts[i].append(w.word)
    return ["".join(p).strip() for p in parts]


def _whisper(languages: tuple[str, ...], **kwargs: Any) -> Any:
    from faster_whisper import WhisperModel
    from faster_whisper.audio import pad_or_trim

    class Whisper(WhisperModel):
        """WhisperModel that only detects `languages` (empty = any) and decodes with the encoder
        pass the detection already made.

        Whisper often labels Hindi or Hinglish speech as Urdu, whose script the scam rules can't
        read. And faster-whisper 1.2 encodes the first 30 s twice when it detects the language,
        which doubled the cost of those windows (the 30 s encoder pass is ~85% of the work).
        """

        _local = threading.local()

        def detect_language(self, audio=None, features=None, **options):
            if features is None:
                return super().detect_language(audio, features, **options)
            encoded = self.encode(pad_or_trim(features[..., :self.feature_extractor.nb_max_frames]))
            probs = [(token[2:-2], p) for token, p in self.model.detect_language(encoded)[0]]
            allowed = [lp for lp in probs if lp[0] in languages] or probs
            self._local.encoded = (features, encoded)
            return allowed[0][0], allowed[0][1] / sum(p for _, p in allowed), probs

        def generate_segments(self, features, tokenizer, options, log_progress=False, encoder_output=None):
            seen, encoded = self._local.__dict__.pop("encoded", (None, None))
            # Same features as the detection (it gets a view of them): its first 30 s are encoded.
            if encoder_output is None and seen is not None and seen.shape == features.shape \
                    and np.shares_memory(seen, features):
                encoder_output = encoded
            return super().generate_segments(features, tokenizer, options, log_progress, encoder_output)

    return Whisper(**kwargs)


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
        s = self.settings
        return _whisper(
            s.whisper_languages,
            model_size_or_path=s.whisper_model,
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
        With neither, Whisper detects it (among WHISPER_LANGUAGES) from the same encoder pass."""
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

    def transcribe_spans(self, audio: np.ndarray, ends: Sequence[int]) -> list[Transcript] | None:
        """A whole clip in one pass, split into the windows that end at sample `ends`.

        The encoder costs the same for 4 s as for 30 s, so window by window an upload paid one
        30 s pass per window; this pays one per 30 s of speech, and Whisper hears whole sentences.
        """
        model = self._slot.get()
        if model is None:
            return None
        s = self.settings
        try:
            segs, info = model.transcribe(
                np.ascontiguousarray(audio, dtype=np.float32),
                language=s.whisper_lang,
                beam_size=s.whisper_beam_size,
                vad_filter=True,
                condition_on_previous_text=False,
                word_timestamps=True,
            )
            texts = split_words((w for seg in segs for w in seg.words or ()), [e / s.sample_rate for e in ends])
        except Exception:
            log.exception("Transcription failed for a clip")
            return None
        lang, prob = info.language, float(info.language_probability or 0.0)
        return [Transcript(t, lang, prob) if t else Transcript("") for t in texts]


_default: WhisperTranscriber | None = None


def transcribe(w: np.ndarray) -> str:
    """Convenience wrapper configured from environment variables (handy for scripts)."""
    global _default
    if _default is None:
        from backend.config import Settings

        _default = WhisperTranscriber(Settings.from_env())
    result = _default.transcribe(w)
    return result.text if result else ""
