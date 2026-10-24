"""What gets stored for one analysed call, built from what the pipeline already has.

Stored: how the call came in (source, consent box, sample rate, file name / type / size),
how long it was, the call-level verdict and a per-window score summary.
Not stored: audio (never), and transcripts unless DB_STORE_TRANSCRIPTS=true.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from backend.schemas import AnalysisResult, Verdict

SOURCES = ("mic", "tab", "live", "file")
END_REASONS = ("stopped", "completed", "disconnected", "idle_timeout", "error")
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
# Long digit runs in a transcript are OTPs, PINs, card or account numbers: never keep them.
_DIGITS_RE = re.compile(r"\d[\d\s-]{2,}\d")


def utc(ts: float) -> datetime:
    """Epoch seconds -> naive UTC datetime, which is what DATETIME(3) columns hold."""
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def hash_client_id(raw: str | None) -> str | None:
    """The browser's anonymous id is stored only as a hash."""
    if not raw or not CLIENT_ID_RE.match(raw):
        return None
    return hashlib.sha256(raw.encode()).hexdigest()


def mask_digits(text: str) -> str:
    return _DIGITS_RE.sub("[number]", text)


def window_summary(r: AnalysisResult) -> dict[str, Any]:
    return {
        "i": r.window_index,
        "t": round(r.timestamp, 2),
        "risk": r.risk,
        "scam": round(r.scam_score, 3),
        "voice": None if r.voice_score is None else round(r.voice_score, 3),
        "flag": r.voice_flag,
        "reasons": r.reasons,
    }


@dataclass
class CallRecord:
    client_session_id: str
    source: str
    end_reason: str
    verdict: Verdict
    ended_at: float                               # epoch seconds
    started_at: float | None = None
    client_hash: str | None = None
    consent_given: bool | None = None
    input_sample_rate: int | None = None
    file_name: str | None = None
    file_type: str | None = None
    file_size_bytes: int | None = None
    audio_seconds: float = 0.0
    windows_analysed: int = 0
    windows_dropped: int = 0
    app_version: str | None = None
    windows: list[dict[str, Any]] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    transcript: str | None = None
    session_uid: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            self.source = "live"
        if self.end_reason not in END_REASONS:
            self.end_reason = "error"
        if self.file_name:
            self.file_name = self.file_name[:255]
        if self.file_type:
            self.file_type = self.file_type[:100]

    @classmethod
    def from_results(
        cls, results: Iterable[AnalysisResult], *, store_transcript: bool = False, **fields: Any
    ) -> CallRecord:
        results = list(results)
        languages: list[str] = []
        for r in results:
            if r.language and r.language not in languages:
                languages.append(r.language)
        transcript = None
        if store_transcript:
            lines = [f"[{r.timestamp:.0f}s] {mask_digits(r.transcript)}" for r in results if r.transcript]
            transcript = "\n".join(lines) or None
        return cls(windows=[window_summary(r) for r in results], languages=languages, transcript=transcript, **fields)

    # -- rows ---------------------------------------------------------------------
    def session_row(self, now: float) -> tuple:
        return (
            self.session_uid, self.client_session_id[:64], self.client_hash, self.source,
            None if self.consent_given is None else int(self.consent_given), self.input_sample_rate,
            self.file_name, self.file_type, self.file_size_bytes, round(self.audio_seconds, 3),
            self.windows_analysed, self.windows_dropped, self.end_reason, self.app_version,
            utc(self.started_at) if self.started_at else None, utc(self.ended_at), utc(now),
        )

    def verdict_row(self, now: float) -> tuple:
        v = self.verdict
        max_risk = max((w["risk"] for w in self.windows), key=_RISK_ORDER.get, default="low")
        return (
            self.session_uid, v.verdict, v.title[:120], v.message[:500], v.scam_score, max_risk,
            json.dumps(v.reasons), int(v.ai_voice), v.voice_score, v.voice_flagged, v.voice_windows,
            v.windows, v.speech_seconds, ",".join(self.languages)[:64] or None,
            json.dumps(self.windows, separators=(",", ":")) if self.windows else None,
            self.transcript, utc(now),
        )
