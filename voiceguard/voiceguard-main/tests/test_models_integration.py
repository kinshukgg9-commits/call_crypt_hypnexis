"""Real-model checks. Opt-in, because they download and load the models:

    VOICEGUARD_MODEL_TESTS=1 pytest -m models
"""
import os

import numpy as np
import pytest

from backend.config import Settings
from backend.transcription import WhisperTranscriber
from ml.anti_spoof import VoiceDetector
from ml.vad import speech_seconds
from tests.helpers import tone

pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(os.getenv("VOICEGUARD_MODEL_TESTS") != "1", reason="set VOICEGUARD_MODEL_TESTS=1"),
]


def test_voice_model_loads_and_scores():
    det = VoiceDetector(Settings.from_env())             # whichever backend is configured
    det.preload()
    assert det.status()["state"] == "ready", det.status()
    score = det.score(tone(4.0))
    assert score is not None and 0.0 <= score <= 1.0


def test_speech_detector_ignores_tones_and_silence():
    assert speech_seconds(tone(4.0)) == 0.0
    assert speech_seconds(np.zeros(64000, np.float32)) == 0.0


def test_whisper_loads_and_stays_quiet_on_silence():
    tr = WhisperTranscriber(Settings.from_env())
    out = tr.transcribe(np.zeros(64000, np.float32))
    assert tr.status()["state"] == "ready", tr.status()
    assert out is not None and out.text == ""
