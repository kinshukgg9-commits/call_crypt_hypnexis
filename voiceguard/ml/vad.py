"""Speech detection with Silero VAD (the ONNX model that ships with faster-whisper).

It takes a few milliseconds per window and tells speech apart from silence, noise and
tones. That matters twice: silent windows can skip speech-to-text, and the anti-spoof
model must only judge real speech (it scores plain noise as "synthetic").
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("voiceguard.vad")
_warned = False


def speech_seconds(audio: np.ndarray, sr: int = 16000) -> float | None:
    """Seconds of speech in mono float32 audio at 16 kHz, or None if the detector is unavailable."""
    global _warned
    if len(audio) == 0:
        return 0.0
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        options = VadOptions(min_silence_duration_ms=100, speech_pad_ms=30)
        chunks = get_speech_timestamps(np.ascontiguousarray(audio, dtype=np.float32), options, sampling_rate=sr)
        return sum(c["end"] - c["start"] for c in chunks) / sr
    except Exception as exc:
        if not _warned:
            _warned = True
            log.warning("Speech detection unavailable (%s: %s); analysing every window.", type(exc).__name__, exc)
        return None
