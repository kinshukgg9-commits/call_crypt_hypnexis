"""Decode uploaded audio files to 16 kHz mono float32.

Decoders are tried in order: PyAV (bundled with faster-whisper, so no system
ffmpeg is needed), the ffmpeg CLI, then soundfile (WAV/FLAC/OGG).
"""
from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import numpy as np

from ml.audio_preprocess import SR, resample, to_mono


class AudioDecodeError(ValueError):
    pass


class _Unavailable(Exception):
    pass


def _decode_pyav(raw: bytes, sr: int) -> np.ndarray:
    try:
        from faster_whisper.audio import decode_audio
    except ImportError:
        raise _Unavailable from None
    return decode_audio(io.BytesIO(raw), sampling_rate=sr)


def _decode_ffmpeg(raw: bytes, sr: int) -> np.ndarray:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise _Unavailable
    proc = subprocess.run(
        [exe, "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(sr),
         "-f", "s16le", "pipe:1"],
        input=raw, capture_output=True, timeout=300, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[:300] or "ffmpeg failed")
    return np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0


def _decode_soundfile(raw: bytes, sr: int) -> np.ndarray:
    try:
        import soundfile as sf
    except (ImportError, OSError):
        raise _Unavailable from None
    data, file_sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    return resample(to_mono(data), int(file_sr), sr)


_DECODERS = (("pyav", _decode_pyav), ("ffmpeg", _decode_ffmpeg), ("soundfile", _decode_soundfile))


def decode_audio_bytes(raw: bytes, sr: int = SR) -> np.ndarray:
    """Any common audio container -> mono float32 at `sr`. Raises AudioDecodeError."""
    if not raw:
        raise AudioDecodeError("The file is empty.")
    tried = []
    for name, decoder in _DECODERS:
        try:
            audio = decoder(raw, sr)
        except _Unavailable:
            continue
        except Exception as exc:
            tried.append(f"{name}: {type(exc).__name__}")
            continue
        audio = np.nan_to_num(np.asarray(audio, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
        return np.clip(audio, -1.0, 1.0)
    if not tried:
        raise AudioDecodeError("No audio decoder is installed (need faster-whisper/PyAV, ffmpeg or soundfile).")
    raise AudioDecodeError(
        "Could not decode this file as audio. Try WAV, MP3, M4A, OGG, FLAC or WEBM. "
        f"({'; '.join(tried)})"
    )


def decode_audio_file(path: str | Path, sr: int = SR) -> np.ndarray:
    return decode_audio_bytes(Path(path).read_bytes(), sr)
