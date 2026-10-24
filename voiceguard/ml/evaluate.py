"""Measure the voice check on your own clips, exactly as the server would judge them.

    python -m ml.evaluate --genuine data/real --spoof data/ai

Put recordings of real people in one folder and AI-generated voices in the other. Each clip
is windowed, speech-gated and scored like an uploaded recording, and judged with the server's
rule (VOICE_THRESHOLD, VOICE_MIN_SHARE, VOICE_STRONG). The report shows how many AI clips were caught, how
many real ones were wrongly flagged, and the EER of the per-clip score.

Test the audio you will really get. Re-recording a voice through a speaker and a microphone,
or adding background noise, hides most of what the model looks for.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

AUDIO_EXTS = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".webm", ".amr", ".aac"}


def eer(y_true, scores) -> tuple[float, float]:
    """Equal error rate and its threshold. y_true: 1 = spoof; higher score = more likely spoof."""
    from sklearn.metrics import roc_curve

    fpr, tpr, thr = roc_curve(y_true, scores)
    fnr = 1 - tpr
    i = int(np.nanargmin(np.abs(fnr - fpr)))
    return float((fpr[i] + fnr[i]) / 2), float(thr[i])


class _NoTranscriber:
    """The voice check does not need the words, so speech-to-text is skipped here."""

    def transcribe(self, w, language=None):
        from backend.transcription import Transcript

        return Transcript("speech")

    def preload(self) -> None:
        pass

    def status(self) -> dict:
        return {"state": "disabled", "detail": "not needed", "error": None}


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXTS)


def main(argv: list[str] | None = None) -> int:
    from backend.audio_io import decode_audio_file
    from backend.config import Settings
    from backend.pipeline import Analyzer
    from ml.anti_spoof import VoiceDetector
    from ml.vad import speech_seconds

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--genuine", type=Path, required=True, help="folder of real voices")
    ap.add_argument("--spoof", type=Path, required=True, help="folder of AI-generated voices")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    detector = VoiceDetector(settings)
    detector.preload()
    if detector.status()["state"] != "ready":
        print(f"Voice model unavailable: {detector.status()['error']}", file=sys.stderr)
        return 1
    analyzer = Analyzer(settings, _NoTranscriber(), detector, speech_seconds if settings.vad_enabled else None)

    labels, shares, flagged = [], [], []
    for label, folder in ((0, args.genuine), (1, args.spoof)):
        for path in _files(folder):
            try:
                _, verdict = analyzer.analyze_clip(decode_audio_file(path), "evaluate")
            except Exception as e:                     # an unreadable file should not stop the run
                print(f"skip (cannot read): {path.name}: {e}")
                continue
            if verdict.voice_score is None:
                print(f"skip (not enough clear speech): {path.name}")
                continue
            labels.append(label)
            shares.append(verdict.voice_score)
            flagged.append(verdict.ai_voice)
            print(f"{'ai  ' if label else 'real'} {'AI-GENERATED' if verdict.ai_voice else 'natural     '} "
                  f"{verdict.voice_flagged:>3}/{verdict.voice_windows:<3} windows flagged  {path.name}")
    y, hit = np.array(labels), np.array(flagged, dtype=bool)
    if len(set(labels)) < 2:
        print("Need at least one usable clip in each folder.", file=sys.stderr)
        return 1
    rate, threshold = eer(y, shares)
    print(f"\nAI clips caught:            {hit[y == 1].sum()} of {(y == 1).sum()}")
    print(f"Real clips wrongly flagged: {hit[y == 0].sum()} of {(y == 0).sum()}")
    print(f"EER of the flagged share:   {rate:.3f} at {threshold:.2f} "
          f"(current rule: VOICE_THRESHOLD={settings.voice_threshold}, VOICE_MIN_SHARE={settings.voice_min_share}, "
          f"VOICE_STRONG={settings.voice_strong})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
