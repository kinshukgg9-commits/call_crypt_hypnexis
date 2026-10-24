"""Risk fusion, audio helpers, decoding, model loading, evaluation and settings."""
import numpy as np
import pytest

from backend.audio_io import AudioDecodeError, decode_audio_bytes
from backend.config import ROOT, Settings
from backend.pipeline import Analyzer, summarize
from backend.risk_engine import fuse
from backend.transcription import Transcript, split_words
from ml.anti_spoof import AntiSpoof, VoiceDetector
from ml.audio_preprocess import check_quality, pcm16_to_float32, resample
from ml.evaluate import eer
from ml.lazy import LazyModel
from tests.helpers import FakeDetector, FakeTranscriber, tone, wav_bytes


# -- risk engine --------------------------------------------------------------------
@pytest.mark.parametrize("scam, risk", [(0.0, "low"), (0.39, "low"), (0.4, "medium"), (0.69, "medium"), (0.7, "high")])
def test_risk_levels(scam, risk):
    assert fuse(scam, 0.1, "ok", voice_threshold=0.8)[0] == risk


def test_voice_flags():
    assert fuse(0, 0.85, "ok", voice_threshold=0.8)[1] == "elevated"
    assert fuse(0, 0.80, "ok", voice_threshold=0.8)[1] == "elevated"
    assert fuse(0, 0.79, "ok", voice_threshold=0.8)[1] == "normal"
    assert fuse(0, None, "ok")[1] == "unavailable"
    assert fuse(0, None, "clipped")[1] == "clipped"
    assert fuse(0, None, "insufficient_audio")[1] == "insufficient_audio"


# -- audio helpers ------------------------------------------------------------------
def test_check_quality():
    assert check_quality(tone(4.0)) == "ok"
    assert check_quality(tone(1.5)) == "insufficient_audio"
    assert check_quality(np.zeros(64000, np.float32)) == "insufficient_audio"
    assert check_quality(np.zeros(0, np.float32)) == "insufficient_audio"
    assert check_quality(np.ones(64000, np.float32)) == "clipped"


def test_pcm16_conversion_ignores_trailing_odd_byte():
    data = np.array([0, 16384, -32768, 32767], "<i2").tobytes() + b"\x01"
    np.testing.assert_allclose(pcm16_to_float32(data), [0, 0.5, -1, 32767 / 32768])


def test_resample_lengths():
    assert len(resample(tone(1.0, sr=48000), 48000)) == 16000
    assert len(resample(tone(1.0, sr=44100), 44100)) == 16000
    x = tone(1.0)
    assert resample(x, 16000) is not None and len(resample(x, 16000)) == 16000


@pytest.mark.parametrize("rate", [8000, 44100, 48000])
def test_resample_keeps_the_signal(rate):
    y = resample(tone(2.0, sr=rate), rate)[2000:-2000]         # away from the edges
    np.testing.assert_allclose(y, tone(2.0)[2000:-2000], atol=0.01)


def test_decode_wav_and_reject_garbage():
    audio = decode_audio_bytes(wav_bytes(tone(2.0, sr=22050), sr=22050, channels=2))
    assert audio.dtype == np.float32 and abs(len(audio) - 32000) <= 16
    with pytest.raises(AudioDecodeError):
        decode_audio_bytes(b"\x00\x01garbage" * 100)
    with pytest.raises(AudioDecodeError):
        decode_audio_bytes(b"")


# -- pipeline -----------------------------------------------------------------------
def test_analyze_clip_carries_context_and_summary():
    settings = Settings(preload_models=False)
    analyzer = Analyzer(settings, FakeTranscriber(["Do not tell anyone", "and read me the OTP"]), FakeDetector(0.9))
    results, verdict = analyzer.analyze_clip(tone(8.0), "clip")
    assert [r.risk for r in results] == ["medium", "high"]
    summary = summarize(results)
    assert summary.risk == "high" and summary.voice_flag == "elevated" and summary.max_voice_score == 0.9
    assert verdict.verdict == "scam" and verdict.final and verdict.ai_voice and verdict.voice_score == 1.0


def test_clip_is_transcribed_in_one_pass_and_split_into_windows():
    class SpanTranscriber(FakeTranscriber):
        def transcribe_spans(self, audio, ends):
            self.spans = (len(audio), list(ends))
            return [Transcript("Do not tell anyone"), Transcript("and read me the OTP")]

    transcriber = SpanTranscriber()
    analyzer = Analyzer(Settings(preload_models=False), transcriber, FakeDetector())
    results, verdict = analyzer.analyze_clip(tone(8.0), "clip")
    assert transcriber.spans == (128000, [64000, 128000]) and transcriber.calls == 0
    assert [r.transcript for r in results] == ["Do not tell anyone", "and read me the OTP"]
    assert [r.risk for r in results] == ["medium", "high"] and verdict.verdict == "scam"


def test_long_clip_is_transcribed_in_30_s_passes_with_fallback():
    class SpanTranscriber(FakeTranscriber):
        passes = []

        def transcribe_spans(self, audio, ends):
            self.passes.append((len(audio), list(ends)))
            return None if len(self.passes) == 2 else [Transcript("hello")] * len(ends)

    transcriber = SpanTranscriber(["fallback"] * 10)
    results, _ = Analyzer(Settings(preload_models=False), transcriber, FakeDetector()).analyze_clip(tone(40.0), "c")
    assert [p[0] for p in transcriber.passes] == [7 * 64000, 3 * 64000]     # 4 s windows: 28 s, then 12 s
    assert transcriber.passes[1][1] == [64000, 128000, 192000]               # ends relative to the pass
    assert [r.transcript for r in results] == ["hello"] * 7 + ["fallback"] * 3   # a failed pass goes window by window


def test_words_go_to_the_window_their_middle_falls_in():
    from types import SimpleNamespace as W

    words = [W(word=" Read", start=0.2, end=0.5), W(word=" me", start=3.8, end=4.1),
             W(word=" the", start=4.0, end=4.4), W(word=" OTP.", start=9.0, end=9.6)]
    assert split_words(words, [4.0, 8.0, 9.5]) == ["Read me", "the", "OTP."]
    assert split_words([], [4.0]) == [""]


def test_language_is_detected_then_reused_then_rechecked():
    settings = Settings(preload_models=False, language_recheck_windows=2)
    transcriber = FakeTranscriber(["", "namaste", "ek", "do", "teen", "char"], language="hi")
    analyzer = Analyzer(settings, transcriber, FakeDetector())
    analyzer.analyze_clip(tone(24.0), "clip")
    # silence teaches nothing; then detect, reuse twice, detect again, reuse
    assert transcriber.hints == [None, None, "hi", "hi", None, "hi"]


def test_low_confidence_detection_is_not_reused():
    from backend.pipeline import LanguageTracker
    from backend.transcription import Transcript

    tracker = LanguageTracker(recheck_every=4, min_probability=0.6)
    tracker.update(None, Transcript("hello", "en", 0.3))
    assert tracker.hint() is None
    tracker.update(None, Transcript("hello", "en", 0.9))
    assert tracker.hint() == "en"
    assert LanguageTracker(recheck_every=0).hint() is None


def test_non_finite_voice_score_becomes_unavailable():
    settings = Settings(preload_models=False)
    analyzer = Analyzer(settings, FakeTranscriber(), FakeDetector(float("nan")))
    result, _ = analyzer.analyze(tone(4.0), [], "s", 0, 4.0)
    assert result.voice_score is None and result.voice_flag == "unavailable"


# -- model loading --------------------------------------------------------------------
def test_lazy_model_remembers_failure_then_retries():
    calls = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("download failed")
        return "model"

    slot = LazyModel("m", loader, retry_after=0.0)
    assert slot.get() is None and slot.state == "error" and "download failed" in slot.error
    assert slot.get() == "model" and slot.state == "ready" and slot.error is None
    assert slot.get() == "model" and len(calls) == 2


def test_lazy_model_does_not_hammer_a_failing_loader():
    calls = []

    def loader():
        calls.append(1)
        raise OSError("x")

    slot = LazyModel("m", loader, retry_after=60)
    slot.get()
    slot.get()
    assert len(calls) == 1


def test_disabled_detector():
    det = VoiceDetector(Settings(antispoof_backend="none"))
    assert det.status()["state"] == "disabled" and det.score(tone(4.0)) is None


def test_missing_weights_are_reported(tmp_path):
    det = VoiceDetector(Settings(
        antispoof_backend="aasist", antispoof_ckpt=tmp_path / "missing.pth", antispoof_model_py=tmp_path / "m.py"))
    assert det.score(tone(4.0)) is None
    status = det.status()
    assert status["state"] == "error" and "fetch_aasist.py" in status["error"]

    det = VoiceDetector(Settings(antispoof_onnx=tmp_path / "missing.onnx"))
    assert det.score(tone(4.0)) is None
    assert det.status()["state"] == "error" and "build_voice_model.py" in det.status()["error"]


@pytest.mark.parametrize("model", ["antideepfake-w2v-small.onnx", "aasist.onnx"])
def test_onnx_voice_models_score_without_pytorch(model):
    settings = Settings(antispoof_onnx=ROOT / "ml" / "models" / model)
    if not settings.antispoof_onnx.is_file():
        pytest.skip(f"ml/models/{model} not present")
    det = VoiceDetector(settings)
    score = det.score(tone(4.0))
    assert det.status()["state"] == "ready" and score is not None and 0.0 <= score <= 1.0
    for seconds in (0.3, 2.5, 9.0):                            # any length is accepted
        assert det.score(tone(seconds)) is not None


def test_antispoof_reads_logits_from_tuple_and_spoof_index():
    torch = pytest.importorskip("torch")

    class TwoClass(torch.nn.Module):
        def forward(self, x):
            assert x.shape == (1, 64600)
            return torch.zeros(1, 8), torch.tensor([[2.0, 0.0]])   # (hidden, logits)

    p_spoof = AntiSpoof(TwoClass(), spoof_index=0).score(tone(4.0))
    p_bonafide = AntiSpoof(TwoClass(), spoof_index=1).score(tone(4.0))
    assert p_spoof == pytest.approx(0.8808, abs=1e-3) and p_spoof + p_bonafide == pytest.approx(1.0)


# -- evaluation -----------------------------------------------------------------------
def test_eer_perfect_and_random():
    rate, _ = eer([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9])
    assert rate == 0.0
    rng = np.random.default_rng(0)
    rate, _ = eer(rng.integers(0, 2, 2000), rng.random(2000))
    assert 0.4 < rate < 0.6


def test_localisation_frames_and_scores():
    from scripts.eval_partial_fakes import best_threshold, counts, f1, frame_windows

    # 3 s clip, 0.5 s frames, 2 s windows: centred, then held inside the clip at the edges
    assert frame_windows(48000, 6, 0.5, 2.0) == [(0, 32000), (0, 32000), (4000, 36000), (12000, 44000),
                                                (16000, 48000), (16000, 48000)]
    assert frame_windows(8000, 1, 0.5, 2.0) == [(0, 8000)]                # shorter than the window
    assert counts("0011", "0110") == (1, 1, 1, 1)
    assert f1(1, 1, 1) == 0.5 and f1(0, 0, 0) == 1.0
    t, best = best_threshold(np.array([False, False, True, True]), np.array([0.1, 0.4, 0.3, 0.9]))
    assert (t, best) == (0.3, 0.8)


def test_robustness_eers_per_family():
    from scripts.eval_voice_robustness import eers, family

    assert family("espeak-mbrola:mb-us1 [courier_customs]") == "espeak" and family("pico:en-GB") == "pico"
    clips = [{"label": 0, "family": "real"}] * 2 + [{"label": 1, "family": "pico"}, {"label": 1, "family": "flite"}]
    out = eers(clips, [0.1, 0.6, 0.9, 0.5])                # pico outscores both real clips, flite does not
    assert out["eer"]["pico"] == 0.0 and out["eer"]["flite"] >= 0.5 and out["unscored"] == 0
    assert eers(clips + [{"label": 1, "family": "pico"}], [0.1, 0.6, 0.9, 0.5, None])["unscored"] == 1


def test_phone_conditions_keep_length_and_change_the_audio():
    pytest.importorskip("scipy")
    from ml.channel import CONDITIONS, _rms, add_noise

    x = tone(2.0)
    for name, condition in CONDITIONS.items():
        y = condition(x)
        assert y.dtype == np.float32 and len(y) == len(x) and np.isfinite(y).all(), name
        if name != "clean":
            assert not np.allclose(y, x, atol=1e-4), name
    noisy = add_noise(x, 10)
    assert 10 * np.log10(_rms(x) ** 2 / _rms(noisy - x) ** 2) == pytest.approx(10, abs=0.5)
    assert np.array_equal(noisy, add_noise(x, 10))                  # the same noise every run


# -- settings -------------------------------------------------------------------------
def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL", "base")
    monkeypatch.setenv("WHISPER_LANG", "hi")
    monkeypatch.setenv("VOICE_THRESHOLD", "0.6")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.example, https://b.example/")
    monkeypatch.setenv("PRELOAD_MODELS", "false")
    s = Settings.from_env()
    assert (s.whisper_model, s.whisper_lang, s.voice_threshold, s.preload_models) == ("base", "hi", 0.6, False)
    assert s.cors_origins == ("http://a.example", "https://b.example")
    assert s.whisper_languages == ("en", "hi")
    monkeypatch.setenv("WHISPER_LANGUAGES", "*")
    assert Settings.from_env().whisper_languages == ()


@pytest.mark.parametrize("name, value", [
    ("MAX_SESSIONS", "lots"), ("VOICE_THRESHOLD", "2"), ("PRELOAD_MODELS", "maybe"), ("ANTISPOOF_BACKEND", "magic"),
])
def test_bad_settings_fail_fast_with_the_variable_name(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.from_env()
