"""One analysis step per audio window, shared by the WebSocket and upload paths.

    1. quality check (silence / clipped / short) and speech detection
    2a. speech-to-text -> recent transcript context -> scam rules -> scam_score + reasons
    2b. anti-spoof model -> voice_score (only on windows with enough real speech)
    3. risk engine -> per-window risk + voice flag
    4. verdict tracker (backend/verdict.py) -> the call-level result
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Callable, Protocol, Sequence

import numpy as np

from backend.config import Settings
from backend.risk_engine import fuse
from backend.scam_rules import score_text
from backend.schemas import AnalysisResult, UploadSummary, Verdict
from backend.transcription import Transcript, WhisperTranscriber
from backend.verdict import VerdictTracker
from ml.anti_spoof import DEFAULT_INPUT_SAMPLES, VoiceDetector
from ml.audio_preprocess import check_quality, find_cut
from ml.vad import speech_seconds

log = logging.getLogger("voiceguard.pipeline")

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
_FLAG_ORDER = ["elevated", "normal", "unavailable", "clipped", "insufficient_audio"]
SPAN_SECONDS = 30.0      # audio per whole-clip transcription pass: Whisper's encoder takes 30 s

SpeechMeter = Callable[[np.ndarray], "float | None"]


class TranscriberLike(Protocol):
    def transcribe(self, w: np.ndarray, language: str | None = None) -> Transcript | None: ...
    def preload(self) -> None: ...
    def status(self) -> dict: ...


class DetectorLike(Protocol):
    def score(self, w: np.ndarray) -> float | None: ...
    def preload(self) -> None: ...
    def status(self) -> dict: ...


class LanguageTracker:
    """Reuses a session's detected language instead of detecting it on every window.

    Detection on 3-4 s of speech is noisy, so a language detected with enough confidence is
    reused, and re-detected every `recheck_every` windows so a call that switches language
    (e.g. Hindi <-> English) is followed. recheck_every=0 always detects.
    """

    def __init__(self, recheck_every: int = 4, min_probability: float = 0.6):
        self.recheck_every = recheck_every
        self.min_probability = min_probability
        self.language: str | None = None
        self._reused = 0

    def hint(self) -> str | None:
        if self.language is not None and self._reused < self.recheck_every:
            return self.language
        return None

    def update(self, hint: str | None, transcript: Transcript | None) -> None:
        if transcript is None or not transcript.text:
            return                                       # nothing learned from silence
        if hint is not None:
            self._reused += 1
            return
        if transcript.language and transcript.language_probability >= self.min_probability:
            self.language = transcript.language
        self._reused = 0


def _finite01(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(min(max(float(value), 0.0), 1.0), 3)


class Analyzer:
    def __init__(
        self,
        settings: Settings,
        transcriber: TranscriberLike,
        detector: DetectorLike,
        speech_meter: SpeechMeter | None = None,
    ):
        """`speech_meter(window) -> seconds of speech` enables speech gating; None analyses every window."""
        self.settings = settings
        self.transcriber = transcriber
        self.detector = detector
        self.speech_meter = speech_meter
        # The voice model is given one full frame (4 s) of continuous audio (see voice_frame()).
        self.voice_samples = settings.antispoof_input_samples or DEFAULT_INPUT_SAMPLES
        # Models are CPU-bound and shared across sessions: cap concurrent analyses.
        self._gate = threading.BoundedSemaphore(settings.max_concurrent_analyses)
        # The 500 ms voice updates skip rather than queue, so they never delay a window's analysis.
        self._voice_gate = threading.BoundedSemaphore(settings.max_concurrent_analyses)

    @classmethod
    def from_settings(cls, settings: Settings) -> Analyzer:
        meter = speech_seconds if settings.vad_enabled else None
        return cls(settings, WhisperTranscriber(settings), VoiceDetector(settings), meter)

    # -- models -----------------------------------------------------------------
    def preload(self) -> None:
        if self.speech_meter is not None:
            self.speech_meter(np.zeros(self.settings.sample_rate, np.float32))   # loads the VAD model
        self.transcriber.preload()
        self.detector.preload()

    def preload_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.preload, name="voiceguard-preload", daemon=True)
        thread.start()
        return thread

    def model_status(self) -> dict[str, dict]:
        return {"transcription": self.transcriber.status(), "anti_spoof": self.detector.status()}

    def new_language_tracker(self) -> LanguageTracker:
        return LanguageTracker(self.settings.language_recheck_windows)

    def new_verdict_tracker(self) -> VerdictTracker:
        return VerdictTracker(self.settings)

    # -- analysis ---------------------------------------------------------------
    def analyze(
        self,
        window: np.ndarray,
        context: Sequence[str],
        session_id: str,
        window_index: int,
        timestamp: float,
        language: LanguageTracker | None = None,
        voice_tail: np.ndarray | None = None,
        transcript: Transcript | None = None,
    ) -> tuple[AnalysisResult, str]:
        """Analyse one window. `context` is the transcript of the previous windows, and
        `voice_tail` the audio just before this window (see voice_frame()). `transcript`, when
        given, is this window's share of a whole-clip transcription and replaces speech-to-text.

        Returns the result and this window's transcript; the caller decides whether
        to append it to the session history (it may have been cleared meanwhile).
        """
        s = self.settings
        with self._gate:
            started = time.perf_counter()
            window = np.asarray(window, dtype=np.float32)
            status = check_quality(window, s.sample_rate)
            speech = self.speech_meter(window) if self.speech_meter is not None and len(window) else None

            if transcript is not None:
                tr: Transcript | None = transcript
            elif speech is not None and speech < s.min_transcribe_speech_seconds:
                tr = Transcript("")                          # no speech: skip speech-to-text
            else:
                hint = language.hint() if language else None
                tr = self.transcriber.transcribe(window, hint)
                if language:
                    language.update(hint, tr)
            if tr is None:
                text, lang, transcript_status = "", None, "unavailable"
            else:
                text, lang = tr.text, tr.language
                transcript_status = "ok" if text else "no_speech"

            recent = [t for t in context if t] + ([text] if text else [])
            scam, reasons = score_text(" ".join(recent))
            voice, status = self._judge_voice(status, speech, self.voice_frame(voice_tail, window))

            risk, flag = fuse(
                scam, voice, status, high=s.risk_high, medium=s.risk_medium, voice_threshold=s.voice_threshold
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)

        result = AnalysisResult(
            session_id=session_id,
            timestamp=round(float(timestamp), 3),
            window_index=window_index,
            duration=round(len(window) / s.sample_rate, 3),
            speech_seconds=None if speech is None else round(speech, 2),
            transcript=text,
            transcript_status=transcript_status,
            language=lang,
            scam_score=float(scam),
            risk=risk,
            reasons=reasons,
            voice_score=voice,
            voice_status=status,
            voice_flag=flag,
            processing_ms=elapsed_ms,
        )
        return result, text

    def _judge_voice(self, status: str, speech: float | None, frame: np.ndarray) -> tuple[float | None, str]:
        """Voice score and status. Only real speech is judged: the model scores noise and tones as "synthetic"."""
        if status == "ok" and speech is not None and speech < self.settings.min_voice_speech_seconds:
            status = "insufficient_audio"
        voice = None
        if status == "ok":
            voice = _finite01(self.detector.score(frame))
            if voice is None:
                status = "unavailable"
        return voice, status

    def voice_now(self, audio: np.ndarray) -> tuple[float | None, str] | None:
        """The live voice update: the latest frame (4 s) of a stream, judged like a window's voice.
        Returns None when other updates already keep the CPU busy; the caller skips this one."""
        if not self._voice_gate.acquire(blocking=False):
            return None
        try:
            frame = np.asarray(audio, dtype=np.float32)[-self.voice_samples:]
            status = check_quality(frame, self.settings.sample_rate)
            speech = self.speech_meter(frame) if self.speech_meter is not None and status == "ok" else None
            return self._judge_voice(status, speech, frame)
        finally:
            self._voice_gate.release()

    def voice_frame(self, tail: np.ndarray | None, window: np.ndarray) -> np.ndarray:
        """The voice model's input: the last `voice_samples` of audio ending with this window.

        Windows end at pauses, so they vary in length. Scoring a short window alone means
        padding it by repetition, which made the scores swing between 0 and 1 on the same
        voice; a full frame of real, continuous audio is stable.
        """
        if tail is None or len(tail) == 0 or len(window) >= self.voice_samples:
            return window[-self.voice_samples:]
        return np.concatenate([tail, window])[-self.voice_samples:]

    def next_voice_tail(self, tail: np.ndarray | None, window: np.ndarray) -> np.ndarray:
        """The tail to pass with the window that follows `window`."""
        return self.voice_frame(tail, window)

    def analyze_clip(self, audio: np.ndarray, session_id: str) -> tuple[list[AnalysisResult], Verdict]:
        """Analyse a whole decoded clip window by window. Returns the windows and the final verdict."""
        s = self.settings
        size, sr = s.window_samples, s.sample_rate
        min_tail = int(s.min_flush_seconds * sr)
        ends: list[int] = []
        start = 0
        while start < len(audio):
            chunk = audio[start:start + size]
            if len(chunk) == size:
                chunk = chunk[:find_cut(chunk, sr, s.min_window_seconds)]     # end at a pause
            if ends and len(chunk) < min_tail:
                break                                             # a sliver of tail adds nothing
            start += len(chunk)
            ends.append(start)

        # One pass per ~30 s of audio when the transcriber can (see WhisperTranscriber.transcribe_spans),
        # taking the gate per pass so a long upload doesn't stall live sessions. Windows of a pass
        # that fails, or with no such transcriber, go through speech-to-text on their own.
        texts: list[Transcript | None] = [None] * len(ends)
        spans = getattr(self.transcriber, "transcribe_spans", None)
        first = 0
        while spans is not None and len(ends) > 1 and first < len(ends):
            begin = ends[first - 1] if first else 0
            last = first
            while last + 1 < len(ends) and ends[last + 1] - begin <= SPAN_SECONDS * sr:
                last += 1
            with self._gate:
                got = spans(audio[begin:ends[last]], [e - begin for e in ends[first:last + 1]])
            if got is not None:
                texts[first:last + 1] = got
            first = last + 1

        history: deque[str] = deque(maxlen=s.context_windows - 1)
        language = self.new_language_tracker()
        verdict = self.new_verdict_tracker()
        results: list[AnalysisResult] = []
        for i, end in enumerate(ends):
            start = ends[i - 1] if i else 0
            tail = audio[max(0, start - self.voice_samples):start]
            result, text = self.analyze(
                audio[start:end], list(history), session_id, i, end / sr, language, tail, texts[i]
            )
            history.append(text)
            verdict.update(result)
            result.verdict = verdict.snapshot()
            results.append(result)
        return results, verdict.snapshot(final=True)


def summarize(results: list[AnalysisResult]) -> UploadSummary:
    reasons: list[str] = []
    for r in results:
        reasons.extend(x for x in r.reasons if x not in reasons)
    voice_scores = [r.voice_score for r in results if r.voice_score is not None]
    flags = {r.voice_flag for r in results}
    return UploadSummary(
        windows=len(results),
        risk=max((r.risk for r in results), key=_RISK_ORDER.__getitem__, default="low"),
        max_scam_score=max((r.scam_score for r in results), default=0.0),
        reasons=reasons,
        max_voice_score=max(voice_scores) if voice_scores else None,
        voice_flag=next((f for f in _FLAG_ORDER if f in flags), "insufficient_audio"),
    )
