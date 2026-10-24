"""The call-level verdict: one result for the whole call, built from every window.

    scam          what was said matches scam tactics strongly enough (voice may or may not be AI)
    ai_voice      the voice looks AI-generated, but nothing said matches scam tactics
    safe          neither
    inconclusive  not enough clear speech, or speech-to-text was unavailable

Why not just use the latest window? A single 4-second window is noisy. Scammers build up
over minutes (authority first, the OTP request later), and one odd voice score should not
flip the result. So:

  * Scam evidence accumulates: every tactic heard stays "active" for EVIDENCE_SECONDS, the
    active tactics combine (noisy-OR), and the call is judged on the strongest moment.
    Once a call reaches the scam verdict it stays there.
  * The voice is judged on the share of speech windows that look synthetic (score at or above
    VOICE_THRESHOLD). The voice model is confident on real voices (they score near 0) but
    catches many AI voices only in some windows, so the test is "at least VOICE_MIN_SHARE of
    the windows, and at least two of them", not an average. One window the model is nearly
    certain about (VOICE_STRONG) is enough on its own: without that, a clip whose first four
    seconds score 0.98 was tagged AI-like on the page and still called "Not a scam call".
    While the call is live it also waits for VERDICT_MIN_VOICE_WINDOWS windows.
"""
from __future__ import annotations

from backend.config import Settings
from backend.messages import MESSAGES
from backend.scam_rules import combine, order
from backend.schemas import AnalysisResult, Verdict


class VerdictTracker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.reset()

    def reset(self) -> None:
        self._last_heard: dict[str, float] = {}     # tactic -> timestamp it was last heard
        self.peak_score = 0.0
        self.voice_scores: list[float] = []
        self.windows = 0
        self.speech_seconds = 0.0
        self._read = 0          # windows whose speech went through speech-to-text
        self._unread = 0        # windows with speech while speech-to-text was unavailable

    def update(self, r: AnalysisResult) -> None:
        self.windows += 1
        for reason in r.reasons:
            self._last_heard[reason] = r.timestamp
        horizon = r.timestamp - self.settings.evidence_seconds
        active = [name for name, heard in self._last_heard.items() if heard >= horizon]
        self.peak_score = max(self.peak_score, combine(active))

        if r.voice_score is not None:
            self.voice_scores.append(r.voice_score)

        # Without a speech measurement, a transcript is the evidence that there was speech.
        speech = r.speech_seconds if r.speech_seconds is not None else (r.duration if r.transcript else 0.0)
        self.speech_seconds += speech
        if r.transcript_status == "unavailable":
            self._unread += 1
        elif speech > 0:
            self._read += 1

    def snapshot(self, final: bool = False) -> Verdict:
        s = self.settings
        copy, notes = MESSAGES["verdict"], MESSAGES["verdict_notes"]
        n = len(self.voice_scores)
        flagged = sum(score >= s.voice_threshold for score in self.voice_scores)
        share = flagged / n if n else None
        needed = 1 if final else s.verdict_min_voice_windows
        # A lone flagged window counts only when it is the only one or a strong one: real voices
        # spike now and then, but seldom that high.
        strong = any(score >= s.voice_strong for score in self.voice_scores)
        ai_voice = n >= needed and (strong or (flagged >= min(2, n) and share >= s.voice_min_share))

        if self.peak_score >= s.verdict_scam_threshold:
            kind = "scam"
            message = copy[kind]["message"] + (f" {notes['voice_ai']}" if ai_voice else "")
        elif self._unread and not self._read:
            kind = "inconclusive"                    # there was audio, but nothing could be read
            message = notes["transcript_unavailable"]
        elif self.speech_seconds < s.verdict_min_speech_seconds:
            kind = "inconclusive"
            message = copy[kind]["message"]
        elif ai_voice:
            kind = "ai_voice"
            message = copy[kind]["message"]
        else:
            kind = "safe"
            if n == 0:
                note = notes["voice_unchecked"]
            elif flagged == 0:
                note = notes["voice_natural"]
            else:
                note = notes["voice_some"]           # some windows flagged: too few, or still being confirmed
            # Weak cues (a lone "right now", "the police") are listed, so don't say nothing matched.
            base = notes["weak_cues"] if self._last_heard else copy[kind]["message"]
            message = f"{base} {note}".strip()

        title = copy[kind]["title"]
        if not final:                                # a call in progress is never declared safe
            title = MESSAGES["verdict_live_titles"].get(kind, title)
        return Verdict(
            verdict=kind,
            title=title,
            message=message,
            final=final,
            scam_score=self.peak_score,
            reasons=order(self._last_heard),
            ai_voice=ai_voice,
            voice_score=None if share is None else round(share, 3),
            voice_flagged=flagged,
            voice_windows=n,
            windows=self.windows,
            speech_seconds=round(self.speech_seconds, 1),
        )
