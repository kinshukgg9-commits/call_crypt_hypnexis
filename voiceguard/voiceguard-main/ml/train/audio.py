"""Audio helpers for training: decode any file to 16 kHz mono, and pass audio through real codecs."""
from __future__ import annotations

import io
import random

import av
import numpy as np

SR = 16000

# name -> (encoder, container, sample rate, bit rates to pick from). These are what speech goes
# through before it reaches us: phone networks, messaging apps, call recorders, streaming.
CODECS = {
    "mp3": ("libmp3lame", "mp3", 16000, (16000, 24000, 32000, 48000, 64000)),
    "mp3_24k": ("libmp3lame", "mp3", 24000, (32000, 48000, 64000)),
    "aac": ("aac", "adts", 16000, (16000, 24000, 32000, 48000)),
    "opus": ("libopus", "ogg", 16000, (8000, 12000, 16000, 24000, 32000)),
    "opus_nb": ("libopus", "ogg", 8000, (6000, 8000, 12000)),
    "amr_nb": ("libopencore_amrnb", "amr", 8000, (4750, 7400, 10200, 12200)),
    "g722": ("g722", "g722", 16000, (None,)),
    "mulaw": ("pcm_mulaw", "wav", 8000, (None,)),
    "alaw": ("pcm_alaw", "wav", 8000, (None,)),
    "g726": ("adpcm_g726", "wav", 8000, (16000, 24000, 32000)),
}


def _to_rate(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return x
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(src, dst)
    return resample_poly(x, dst // g, src // g).astype(np.float32)


def _read(container, rate: int) -> np.ndarray:
    """All audio in the container as int16 mono at `rate` (numpy does the converting: PyAV's
    resampler crawls when the CPU is busy)."""
    chunks, src = [], rate
    for frame in container.decode(audio=0):
        a = frame.to_ndarray()
        if a.dtype.kind in "iu":
            a = a.astype(np.float32) / float(np.iinfo(a.dtype).max + 1) if a.dtype != np.uint8 else (a.astype(np.float32) - 128) / 128
        a = a.astype(np.float32, copy=False)
        if not frame.format.is_planar and frame.layout.nb_channels > 1:
            a = a.reshape(-1, frame.layout.nb_channels).T
        chunks.append(a.mean(0) if a.ndim > 1 else a)
        src = frame.sample_rate
    if not chunks:
        return np.zeros(0, np.int16)
    x = _to_rate(np.concatenate(chunks), src, rate)
    return (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)


def decode(source, rate: int = SR) -> np.ndarray:
    """A file path or bytes -> int16 mono at `rate`."""
    with av.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else str(source)) as container:
        return _read(container, rate)


def through_codec(pcm: np.ndarray, name: str, bitrate: int | None = None) -> np.ndarray:
    """int16 mono 16 kHz -> the same audio after encoding with `name` and decoding again (same length)."""
    encoder, fmt, rate, rates = CODECS[name]
    buffer = io.BytesIO()
    with av.open(buffer, "w", format=fmt) as out:
        stream = out.add_stream(encoder, rate=rate, layout="mono")
        chosen = bitrate or random.choice(rates)
        if chosen:
            stream.bit_rate = chosen
        if encoder == "libopus":
            stream.options = {"application": "voip"}
        want = stream.codec_context.format.name                       # hand the encoder its own format
        x = _to_rate(pcm.astype(np.float32) / 32768.0, SR, rate)
        data = (np.clip(x, -1, 1) * 32767).astype(np.int16) if want.startswith("s16") else             (np.clip(x, -1, 1).astype(np.float64) * 2147483647).astype(np.int32) if want.startswith("s32") else x.astype(np.float32)
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data[None]), format=want, layout="mono")
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    buffer.seek(0)
    with av.open(buffer, format=fmt if fmt in ("g722",) else None) as container:
        back = _read(container, SR)
    if len(back) >= len(pcm):
        return back[: len(pcm)]
    return np.concatenate([back, np.zeros(len(pcm) - len(back), np.int16)])


def random_codec(pcm: np.ndarray, rng: random.Random) -> np.ndarray:
    name = rng.choice(list(CODECS))
    try:
        return through_codec(pcm, name, rng.choice(CODECS[name][3]))
    except Exception:
        return pcm
