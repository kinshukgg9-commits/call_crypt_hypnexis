"""Turns the content score and the voice score into the labels the UI shows.

The two signals are kept separate on purpose; add a combined banner only after
testing shows it helps.
"""
from __future__ import annotations

import os

HIGH, MED = 0.70, 0.40
VOICE_THRESH = float(os.getenv("VOICE_THRESHOLD", "0.50"))   # a window "looks synthetic" at or above this


def risk_level(scam: float, high: float = HIGH, medium: float = MED) -> str:
    return "high" if scam >= high else "medium" if scam >= medium else "low"


def fuse(
    scam: float,
    voice: float | None,
    status: str,
    *,
    high: float = HIGH,
    medium: float = MED,
    voice_threshold: float | None = None,
) -> tuple[str, str]:
    """Return (risk, voice_flag).

    status is the voice status: "ok" | "insufficient_audio" | "clipped" | "unavailable".
    A voice score is only judged when status is "ok"; otherwise the status itself is the flag.
    """
    threshold = VOICE_THRESH if voice_threshold is None else voice_threshold
    risk = risk_level(scam, high, medium)
    if status != "ok":
        flag = status
    elif voice is None:
        flag = "unavailable"
    else:
        flag = "elevated" if voice >= threshold else "normal"
    return risk, flag
