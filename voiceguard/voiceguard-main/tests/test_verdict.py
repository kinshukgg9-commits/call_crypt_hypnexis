"""The call-level verdict, speech gating and pause-aligned windows."""
import numpy as np

from backend.config import Settings
from backend.pipeline import Analyzer
from backend.schemas import AnalysisResult
from backend.verdict import VerdictTracker
from ml.audio_preprocess import find_cut
from tests.helpers import FakeDetector, FakeTranscriber, tone

SR = 16000


def window(t, reasons=(), voice=0.1, transcript="hello there", status="ok", speech=3.0):
    return AnalysisResult(
        session_id="s", timestamp=t, window_index=int(t // 4), duration=4.0, speech_seconds=speech,
        transcript=transcript, transcript_status=status, language="en", scam_score=0.0, risk="low",
        reasons=list(reasons), voice_score=voice, voice_status="ok" if voice is not None else "insufficient_audio",
        voice_flag="normal", processing_ms=1,
    )


def run(windows, final=True, **settings):
    tracker = VerdictTracker(Settings(preload_models=False, **settings))
    for w in windows:
        tracker.update(w)
    return tracker.snapshot(final=final)


# -- the three outcomes -----------------------------------------------------------------
def test_safe_call():
    v = run([window(4), window(8)])
    assert (v.verdict, v.title, v.ai_voice, v.reasons) == ("safe", "Not a scam call", False, [])
    assert v.message.endswith("No signs of an AI-generated voice were found.")


def test_ai_voice_without_scam_content():
    v = run([window(4, voice=0.95), window(8, voice=0.9), window(12, voice=0.97)])
    assert (v.verdict, v.ai_voice, v.voice_score, v.voice_flagged, v.voice_windows) == ("ai_voice", True, 1.0, 3, 3)
    assert v.title == "AI-generated voice, likely not a scam"


def test_scam_call_with_human_and_with_ai_voice():
    human = run([window(4, ["Sensitive info request"])])
    assert human.verdict == "scam" and not human.ai_voice and "AI-generated" not in human.message
    ai = run([window(4, ["Sensitive info request"], voice=0.95)])
    assert ai.verdict == "scam" and ai.ai_voice and "AI-generated" in ai.message


# -- scam evidence ------------------------------------------------------------------------
def test_weak_cues_alone_never_convict():
    for reason in ("Authority impersonation", "Urgency", "Money movement", "Threat or confinement", "Secrecy"):
        assert run([window(4, [reason])]).verdict == "safe", reason
    weak = run([window(4, ["Money movement", "Urgency"])])                           # "send it right now"
    assert weak.verdict == "safe" and weak.reasons == ["Money movement", "Urgency"]
    assert "resembled scam tactics" in weak.message and "Nothing said" not in weak.message
    assert run([window(4, ["Authority impersonation", "Money movement"])]).verdict == "safe"   # "pay income tax"


def test_cues_spread_across_the_call_add_up():
    v = run([window(4, ["Authority impersonation"]), window(8), window(60, ["Threat or confinement"])])
    assert v.verdict == "scam" and v.scam_score == 0.66
    assert v.reasons == ["Threat or confinement", "Authority impersonation"]


def test_old_evidence_expires():
    v = run([window(4, ["Authority impersonation"]), window(300, ["Threat or confinement"])], evidence_seconds=120)
    assert v.verdict == "safe" and v.scam_score == 0.55
    assert v.reasons == ["Threat or confinement", "Authority impersonation"]      # still listed as noticed


def test_scam_verdict_is_sticky():
    v = run([window(4, ["Sensitive info request"])] + [window(4 * i) for i in range(2, 100)])
    assert v.verdict == "scam" and v.scam_score == 0.6


# -- voice ------------------------------------------------------------------------------
def test_one_odd_voice_window_does_not_flip_the_result():
    v = run([window(4, voice=0.1), window(8, voice=0.8), window(12, voice=0.2)])
    assert v.verdict == "safe" and (v.voice_flagged, v.voice_score) == (1, 0.333)
    assert "No signs" not in v.message                         # it isn't called natural either,
    assert v.message.endswith("too little to call it AI-generated.")   # and the page says what it saw


def test_one_strong_voice_window_is_enough():
    scores = [0.98, 0.07, 0.04, 0.28]                          # an AI clip the model caught once, firmly
    v = run([window(4 * i, voice=x) for i, x in enumerate(scores, 1)])
    assert v.verdict == "ai_voice" and (v.voice_flagged, v.voice_windows, v.voice_score) == (1, 4, 0.25)
    assert run([window(4 * i, voice=x) for i, x in enumerate([0.94] + scores[1:], 1)]).verdict == "safe"
    off = run([window(4 * i, voice=x) for i, x in enumerate(scores, 1)], voice_strong=1.01)
    assert off.verdict == "safe" and not off.ai_voice
    assert run([window(4, voice=0.6), window(8, voice=0.1), window(12, voice=0.1)], voice_strong=0.5).ai_voice


def test_voice_that_is_only_caught_in_some_windows_still_counts():
    scores = [0.9, 0.1, 0.85, 0.05, 0.0, 0.2]                  # 2 of 6: a third looks synthetic
    v = run([window(4 * i, voice=x) for i, x in enumerate(scores, 1)])
    assert v.verdict == "ai_voice" and (v.voice_flagged, v.voice_windows, v.voice_score) == (2, 6, 0.333)
    v = run([window(4 * i, voice=x) for i, x in enumerate(scores + [0.1], 1)])      # 2 of 7: not enough
    assert v.verdict == "safe" and not v.ai_voice
    assert run([window(4 * i, voice=x) for i, x in enumerate(scores, 1)], voice_min_share=0.5).verdict == "safe"


def test_a_window_is_flagged_at_the_voice_threshold():
    assert run([window(4, voice=0.5), window(8, voice=0.5)]).voice_flagged == 2
    assert run([window(4, voice=0.49), window(8, voice=0.49)]).voice_flagged == 0
    assert run([window(4, voice=0.6), window(8, voice=0.6)], voice_threshold=0.7).verdict == "safe"


def test_live_ai_verdict_waits_for_two_voice_windows():
    live = run([window(4, voice=0.95)], final=False)
    assert live.verdict == "safe" and not live.ai_voice and "No signs" not in live.message
    assert run([window(4, voice=0.95)], final=True).verdict == "ai_voice"     # a short clip still gets judged
    assert run([window(4, voice=0.95), window(8, voice=0.9)], final=False).verdict == "ai_voice"
    assert run([window(4, voice=0.99)], final=False).verdict == "safe"        # even a strong one waits
    assert run([window(4, voice=0.99), window(8, voice=0.1)], final=False).verdict == "ai_voice"


def test_running_verdicts_are_worded_as_provisional():
    assert run([window(4)], final=False).title == "No scam detected so far"
    assert run([window(4)], final=True).title == "Not a scam call"
    live_ai = run([window(4, voice=0.95), window(8, voice=0.9)], final=False)
    assert live_ai.title == "AI-generated voice, no scam detected so far"
    assert run([window(4, ["Sensitive info request"])], final=False).title == "Scam call detected"
    assert run([], final=False).title == "Listening…"


def test_voice_never_assessed_is_said_plainly():
    v = run([window(4, voice=None), window(8, voice=None)])
    assert v.verdict == "safe" and v.voice_score is None
    assert v.message.endswith("Voice authenticity could not be checked.")


# -- inconclusive -------------------------------------------------------------------------
def test_no_speech_is_inconclusive():
    v = run([window(4, transcript="", status="no_speech", voice=None, speech=0.0)] * 3)
    assert (v.verdict, v.title) == ("inconclusive", "Not enough to judge")
    assert run([]).verdict == "inconclusive"


def test_transcription_down_is_inconclusive_even_with_ai_voice():
    v = run([window(4, transcript="", status="unavailable", voice=0.95)] * 3)
    assert v.verdict == "inconclusive" and "Speech recognition is unavailable" in v.message


# -- speech gating in the analyzer ----------------------------------------------------------
def analyzer_with_speech(seconds, texts=("hello",)):
    transcriber, detector = FakeTranscriber(texts), FakeDetector(0.95)
    return Analyzer(Settings(preload_models=False), transcriber, detector, lambda w: seconds), transcriber, detector


def test_silent_window_skips_both_models():
    analyzer, transcriber, detector = analyzer_with_speech(0.0)
    result, text = analyzer.analyze(tone(4.0), [], "s", 0, 4.0)
    assert (transcriber.calls, detector.calls, text) == (0, 0, "")
    assert result.transcript_status == "no_speech" and result.voice_flag == "insufficient_audio"
    assert result.speech_seconds == 0.0 and result.voice_score is None


def test_a_little_speech_is_transcribed_but_voice_is_not_judged():
    analyzer, transcriber, detector = analyzer_with_speech(0.8)
    result, _ = analyzer.analyze(tone(4.0), [], "s", 0, 4.0)
    assert (transcriber.calls, detector.calls) == (1, 0)
    assert result.transcript == "hello" and result.voice_status == "insufficient_audio"


def test_enough_speech_is_fully_analysed():
    analyzer, transcriber, detector = analyzer_with_speech(3.2)
    result, _ = analyzer.analyze(tone(4.0), [], "s", 0, 4.0)
    assert (transcriber.calls, detector.calls) == (1, 1)
    assert result.voice_score == 0.95 and result.voice_flag == "elevated" and result.speech_seconds == 3.2


def test_noise_only_recording_is_inconclusive_not_ai():
    analyzer, _, _ = analyzer_with_speech(0.0)
    _, verdict = analyzer.analyze_clip(tone(12.0), "clip")
    assert verdict.verdict == "inconclusive" and not verdict.ai_voice


def test_voice_model_gets_a_full_frame_of_continuous_audio():
    analyzer, _, detector = analyzer_with_speech(3.0)
    first, second = tone(3.2), tone(3.1, amp=0.2)
    assert np.array_equal(analyzer.voice_frame(None, first), first)                       # nothing before it yet
    frame = analyzer.voice_frame(analyzer.next_voice_tail(None, first), second)
    assert len(frame) == 64000 and np.array_equal(frame[-len(second):], second)
    assert np.array_equal(frame[:-len(second)], first[-(64000 - len(second)):])
    analyzer.analyze_clip(np.concatenate([first, second]), "clip")
    assert detector.lengths[-1] == 64000


# -- pause-aligned windows ------------------------------------------------------------------
def test_find_cut_ends_at_a_pause():
    x = tone(4.0)
    x[int(3.3 * SR):int(3.5 * SR)] = 0.0
    cut = find_cut(x, SR, 3.0)
    assert int(3.3 * SR) < cut < int(3.5 * SR)


def test_find_cut_without_a_pause_uses_the_whole_window():
    assert find_cut(tone(4.0), SR, 3.0) == 4 * SR
    assert find_cut(np.zeros(4 * SR, np.float32), SR, 3.0) == 4 * SR
    assert find_cut(tone(3.0), SR, 3.0) == 3 * SR                    # nothing after the minimum


def test_find_cut_ignores_pauses_before_the_minimum():
    x = tone(4.0)
    x[int(1.0 * SR):int(1.3 * SR)] = 0.0
    assert find_cut(x, SR, 3.0) == 4 * SR


def test_clip_windows_follow_pauses_and_cover_all_audio():
    audio = tone(10.0)
    audio[int(3.5 * SR):int(3.7 * SR)] = 0.0
    analyzer = Analyzer(Settings(preload_models=False), FakeTranscriber(), FakeDetector())
    results, _ = analyzer.analyze_clip(audio, "clip")
    assert 3.5 < results[0].timestamp < 3.7
    assert abs(sum(r.duration for r in results) - 10.0) < 0.01 and results[-1].timestamp == 10.0
