"""Audio helpers shared by the streaming and upload paths. All audio is float32 mono in [-1, 1]."""
from __future__ import annotations

from math import gcd

import numpy as np

SR = 16000


def check_quality(w: np.ndarray, sr: int = SR) -> str:
    """Return "ok" | "insufficient_audio" | "clipped" for a mono float32 window."""
    if len(w) < sr * 2:
        return "insufficient_audio"
    if float(np.sqrt(np.mean(np.square(w, dtype=np.float64)))) < 0.005:
        return "insufficient_audio"                                      # near silence
    if float(np.mean(np.abs(w) > 0.99)) > 0.01:
        return "clipped"
    return "ok"


def find_cut(x: np.ndarray, sr: int, min_seconds: float, pause_ms: int = 100, dip: float = 0.3) -> int:
    """Where to end a window so that it doesn't split a word.

    Finds the quietest `pause_ms` stretch after `min_seconds`. If it is a clear pause (well
    below the window's overall level) the window ends in the middle of it; otherwise the
    whole of `x` is used. Returns a sample count in (min_seconds * sr, len(x)].
    """
    n, lo, win = len(x), int(min_seconds * sr), max(1, int(sr * pause_ms / 1000))
    if n - lo < win:
        return n
    sq = np.square(x, dtype=np.float64)
    overall = float(np.sqrt(sq.mean()))
    if overall < 1e-4:
        return n                                              # silence: nothing to protect
    csum = np.concatenate(([0.0], np.cumsum(sq[lo:])))
    starts = np.arange(0, n - lo - win + 1, max(1, win // 5))
    energy = (csum[starts + win] - csum[starts]) / win
    quietest = int(np.argmin(energy))
    if float(np.sqrt(energy[quietest])) >= dip * overall:
        return n                                              # no real pause: plain cut
    return lo + int(starts[quietest]) + win // 2


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """Little-endian signed 16-bit PCM bytes -> float32. A trailing odd byte is ignored."""
    usable = len(data) - (len(data) % 2)
    return np.frombuffer(data[:usable], dtype="<i2").astype(np.float32) / 32768.0


def resample(x: np.ndarray, sr_in: int, sr_out: int = SR) -> np.ndarray:
    """Anti-aliased resampling. Returns float32 with round(len(x) * sr_out / sr_in) samples."""
    if sr_in == sr_out or len(x) == 0:
        return np.asarray(x, dtype=np.float32)
    x = np.ascontiguousarray(x, dtype=np.float32)
    try:
        y = _resample_ffmpeg(x, sr_in, sr_out)
    except ImportError:                       # PyAV comes with faster-whisper; SciPy is the fallback
        from scipy.signal import resample_poly

        g = gcd(sr_in, sr_out)
        y = resample_poly(x.astype(np.float64), sr_out // g, sr_in // g)
    want = int(round(len(x) * sr_out / sr_in))
    y = np.asarray(y, dtype=np.float32)[:want]
    if len(y) < want:
        y = np.pad(y, (0, want - len(y)))
    return np.clip(y, -1.0, 1.0)


def _resample_ffmpeg(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """FFmpeg's resampler through PyAV, which is loaded anyway (importing SciPy costs ~70 MB)."""
    import av

    resampler = av.AudioResampler(format="flt", layout="mono", rate=sr_out)
    frame = av.AudioFrame.from_ndarray(x[None, :], format="flt", layout="mono")
    frame.sample_rate = sr_in
    frames = resampler.resample(frame) + resampler.resample(None)      # None flushes the tail
    if not frames:
        return np.zeros(0, np.float32)
    return np.concatenate([f.to_ndarray().reshape(-1) for f in frames])


def to_mono(x: np.ndarray) -> np.ndarray:
    """(samples,) or (samples, channels) -> (samples,) float32."""
    x = np.asarray(x, dtype=np.float32)
    return x.mean(axis=1, dtype=np.float32) if x.ndim == 2 else x
