"""Risk fusion, audio helpers, decoding, model loading, evaluation and settings."""
import numpy as np
import pytest

from backend.audio_io import AudioDecodeError, decode_audio_bytes
from backend.config import ROOT, Settings
from backend.pipeline import Analyzer, summarize
from backend.risk_engine import fuse
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


@pytest.mark.parametrize("name, value", [
    ("MAX_SESSIONS", "lots"), ("VOICE_THRESHOLD", "2"), ("PRELOAD_MODELS", "maybe"), ("ANTISPOOF_BACKEND", "magic"),
])
def test_bad_settings_fail_fast_with_the_variable_name(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.from_env()
