"""Phone-line conditions, for measuring the voice check on the audio a phone call really delivers.

Each condition takes float32 mono 16 kHz audio and returns the same number of samples at 16 kHz,
as the server would receive it after the line:

    clean        the audio as it is
    resample_8k  down to 8 kHz and back up: a narrow-band line with no codec
    g711_mulaw   G.711 mu-law at 8 kHz, the fixed-line and VoIP phone codec
    amr_nb       AMR-NB at 12.2 kbit/s, the GSM / 3G mobile codec
    noise_10db   background noise (pink hiss) at 10 dB SNR
    phone_worst  noise at 10 dB SNR, then AMR-NB at 7.4 kbit/s (a weak mobile link)

The codecs are the real ones (FFmpeg through PyAV, as in ml/train/audio.py), not simulations.
The noise is seeded, so a condition gives the same audio on every run.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from ml.audio_preprocess import resample
from ml.train.audio import through_codec

SR = 16000


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def add_noise(x: np.ndarray, snr_db: float, seed: int = 0) -> np.ndarray:
    """Pink noise `snr_db` below the audio's level (RMS over the whole clip)."""
    if not len(x):
        return np.asarray(x, dtype=np.float32)
    spectrum = np.fft.rfft(np.random.default_rng(seed).standard_normal(len(x)))
    spectrum /= np.sqrt(np.maximum(np.fft.rfftfreq(len(x), 1 / SR), 20.0))        # 1/f power
    noise = np.fft.irfft(spectrum, n=len(x))
    noise *= _rms(x) / max(_rms(noise), 1e-12) * 10 ** (-snr_db / 20)
    return np.clip(x + noise, -1.0, 1.0).astype(np.float32)


def codec(x: np.ndarray, name: str, bitrate: int | None = None) -> np.ndarray:
    """Encode with a codec from ml/train/audio.py CODECS and decode again."""
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
    return through_codec(pcm, name, bitrate).astype(np.float32) / 32768.0


def narrowband(x: np.ndarray) -> np.ndarray:
    y = resample(resample(x, SR, 8000), 8000, SR)
    return np.pad(y, (0, max(0, len(x) - len(y))))[: len(x)]


CONDITIONS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "clean": lambda x: np.asarray(x, dtype=np.float32),
    "resample_8k": narrowband,
    "g711_mulaw": lambda x: codec(x, "mulaw"),
    "amr_nb": lambda x: codec(x, "amr_nb", 12200),
    "noise_10db": lambda x: add_noise(x, 10),
    "phone_worst": lambda x: codec(add_noise(x, 10), "amr_nb", 7400),
}
