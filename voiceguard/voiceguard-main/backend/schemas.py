"""The JSON contract shared with the frontend.

The call-level Verdict is what a result page shows. The per-window result keeps every field of the original contract
(session_id, timestamp, transcript, scam_score, risk, reasons, voice_score,
voice_status, voice_flag) and adds a few fields the UI needs to render every
state honestly (type, window_index, transcript_status, language, processing_ms).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

VerdictKind = Literal["safe", "ai_voice", "scam", "inconclusive"]
Risk = Literal["low", "medium", "high"]
VoiceStatus = Literal["ok", "insufficient_audio", "clipped", "unavailable"]
VoiceFlag = Literal["normal", "elevated", "insufficient_audio", "clipped", "unavailable"]
TranscriptStatus = Literal["ok", "no_speech", "unavailable"]
ModelState = Literal["disabled", "not_loaded", "loading", "ready", "error"]


class Verdict(BaseModel):
    """The call-level result. The three main outcomes are safe, ai_voice and scam;
    inconclusive means there was not enough clear speech (or speech-to-text was down)."""

    verdict: VerdictKind
    title: str = Field(description="Headline to show, e.g. 'Scam call detected'.")
    message: str = Field(description="One or two sentences of guidance to show under the headline.")
    final: bool = Field(description="False while the call is still being analysed.")
    scam_score: float = Field(ge=0.0, le=1.0, description="Strongest scam evidence seen during the call.")
    reasons: list[str] = Field(description="Scam tactics noticed during the call, strongest first.")
    ai_voice: bool = Field(description="True when the voice looks AI-generated (can also be true for scam).")
    voice_score: float | None = Field(
        description="Share (0-1) of assessed windows that look synthetic; null if none were assessed."
    )
    voice_flagged: int = Field(default=0, description="Assessed windows that look synthetic.")
    voice_windows: int = Field(description="Windows with enough speech to assess the voice.")
    windows: int
    speech_seconds: float = Field(description="Seconds of speech analysed so far.")


class AnalysisResult(BaseModel):
    type: Literal["result"] = "result"
    session_id: str
    timestamp: float = Field(description="Seconds of audio from session start to the end of this window.")
    window_index: int = Field(description="0-based sequence number of the window within the session.")
    duration: float = Field(description="Length of this window in seconds (3-4 s; windows end at a pause).")
    speech_seconds: float | None = Field(description="Speech detected in this window; null if not measured.")
    transcript: str = Field(description="Text heard in this window (may be empty).")
    transcript_status: TranscriptStatus
    language: str | None = Field(description="Language detected by speech-to-text, when there was speech.")
    scam_score: float = Field(ge=0.0, le=1.0, description="Uncalibrated scam-cue score over the recent context.")
    risk: Risk
    reasons: list[str] = Field(description="Scam-cue categories found in the recent context.")
    voice_score: float | None = Field(
        ge=0.0, le=1.0, description="Uncalibrated synthetic-speech score; null when not assessed."
    )
    voice_status: VoiceStatus
    voice_flag: VoiceFlag
    processing_ms: int = Field(ge=0, description="Server time spent analysing this window.")
    verdict: Verdict | None = Field(default=None, description="Running call-level verdict after this window.")


class UploadSummary(BaseModel):
    windows: int
    risk: Risk = Field(description="Highest risk seen in any window.")
    max_scam_score: float
    reasons: list[str] = Field(description="Every scam-cue category seen, in first-seen order.")
    max_voice_score: float | None
    voice_flag: VoiceFlag = Field(description="'elevated' if any window was elevated, else the best status seen.")


class UploadAnalysis(BaseModel):
    session_id: str
    filename: str | None
    duration: float = Field(description="Decoded audio duration in seconds.")
    window_seconds: float
    verdict: Verdict = Field(description="The final result for the whole recording.")
    results: list[AnalysisResult]
    summary: UploadSummary


class ModelStatus(BaseModel):
    state: ModelState
    detail: str
    error: str | None = None


class StatusResponse(BaseModel):
    ok: bool
    version: str
    active_sessions: int
    max_sessions: int
    models: dict[str, ModelStatus]


class AudioConfig(BaseModel):
    sample_rate: int
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    channels: int = 1
    window_seconds: float
    recommended_frame_ms: int
    min_input_sample_rate: int
    max_input_sample_rate: int


class ThresholdConfig(BaseModel):
    scam_verdict: float
    risk_high: float
    risk_medium: float
    voice: float = Field(description="A window looks synthetic at or above this voice_score.")
    voice_share: float = Field(description="Share of such windows that makes the verdict's ai_voice true.")
    voice_strong: float = Field(description="One window at or above this voice_score is enough on its own.")


class LimitConfig(BaseModel):
    max_upload_mb: float
    max_upload_seconds: float
    max_frame_bytes: int
    idle_timeout_seconds: float
    max_sessions: int


class ConfigResponse(BaseModel):
    version: str
    audio: AudioConfig
    thresholds: ThresholdConfig
    limits: LimitConfig
    messages: dict[str, Any]
    endpoints: dict[str, str]


class ErrorResponse(BaseModel):
    detail: str
    code: str
