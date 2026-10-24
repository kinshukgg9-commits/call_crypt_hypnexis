"""EER of the voice check over phone lines (Phase 2) and on clean 2-10 s clips (Phase 1), on benchmark/vgsc25.

    python scripts/eval_voice_robustness.py                    # -> benchmark/vgsc25/results/robustness.json
    python scripts/eval_voice_robustness.py --save-baseline    # also record the Phase 1 EERs for later runs
    python scripts/eval_voice_robustness.py --limit 60         # a quick look; writes nothing

The clips, all 2-10 s as in Phase 1, cut from the benchmark audio:
  genuine    the 30 genuine partial-fake controls, and the real stretches (2-10 s) of the other
             partial fakes: three LibriSpeech readers
  synthetic  the synthetic stretches (2-10 s) of the partial fakes, and the turns (2-10 s) of the
             clean call renderings: espeak, festival, flite and pico voices, none of which the
             voice model was trained on

Each clip goes through every condition in ml/channel.py (8 kHz, G.711, AMR-NB, noise) and is
scored as an uploaded recording: the server's windows, each judged on 4 s of audio with speech
detection, and the clip's score is the mean of its window scores. Target: the worst of 8 kHz,
G.711, AMR-NB and noise at most 2.5x the clean EER (phone_worst, all at once, is reported beside).

Phase 1 comparison, on the clean clips:
  streaming  each clip played as a live call, scored by the 500 ms voice updates
             (Analyzer.voice_now on the latest 4 s); clip score = mean of the updates. Its EERs
             must stay within 2 points of the window EERs.
  baseline   --save-baseline writes the clean EERs to phase1_baseline.json; every later run
             fails if one of them rises more than 2 points above it.
EERs are given over all clips and per synthetic voice family (that family against all genuine
clips). Exit status 1 when a target is missed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BENCH = ROOT / "benchmark" / "vgsc25"
SR = 16000
MIN_SECONDS, MAX_SECONDS = 2.0, 10.0   # Phase 1 clips
TARGET_RATIO = 2.5                     # worst phone-line EER / clean EER
# The conditions the target covers; phone_worst (noise and a low-rate codec together) is reported as a stress case.
TARGET_CONDITIONS = ("resample_8k", "g711_mulaw", "amr_nb", "noise_10db")
MAX_DRIFT = 0.02                       # Phase 1 EERs within 2 points


def family(source: str) -> str:
    """'espeak-mbrola:mb-us1 [courier_customs]' -> 'espeak'."""
    return source.split(":")[0].split("-")[0]


def load_clips(limit: int = 0) -> list[dict]:
    """The Phase 1 clips: genuine (label 0) or synthetic (label 1), float32 at 16 kHz."""
    import soundfile as sf

    def read(path: Path) -> np.ndarray:
        audio, sr = sf.read(str(path), dtype="float32")
        if sr != SR:
            raise SystemExit(f"{path} is {sr} Hz, expected {SR}")
        return audio

    clips = []
    pf = json.loads((BENCH / "partial_fakes" / "manifest.json").read_text(encoding="utf-8"))
    for clip in pf["clips"]:
        audio = read(BENCH / "partial_fakes" / clip["file"])
        if not any(s["label"] == "fake" for s in clip["segments"]):
            clips.append({"id": clip["name"], "label": 0, "family": "real", "audio": audio})
            continue
        for k, s in enumerate(clip["segments"]):
            if MIN_SECONDS <= s["end"] - s["start"] <= MAX_SECONDS:
                fake = s["label"] == "fake"
                clips.append({"id": f"{clip['name']}#{k}", "label": int(fake),
                              "family": family(s["source"]) if fake else "real",
                              "audio": audio[int(s["start"] * SR):int(s["end"] * SR)]})
    calls = json.loads((BENCH / "audio" / "manifest.json").read_text(encoding="utf-8"))
    for clip in calls["clips"]:
        if clip["condition"] != "clean":
            continue
        audio = read(BENCH / "audio" / clip["file"])
        for k, t in enumerate(clip["turns"]):
            if MIN_SECONDS <= t["end"] - t["start"] <= MAX_SECONDS:
                clips.append({"id": f"{clip['call_id']}#{k}", "label": 1, "family": family(t["voice"]),
                              "audio": audio[int(t["start"] * SR):int(t["end"] * SR)]})
    if limit:
        rng = np.random.default_rng(0)
        keep = []
        for label in (0, 1):
            group = [c for c in clips if c["label"] == label]
            keep += [group[i] for i in sorted(rng.choice(len(group), min(len(group), limit // 2), replace=False))]
        clips = keep
    return clips


def window_score(analyzer, audio: np.ndarray) -> float | None:
    """The clip as an uploaded recording: the mean voice score of the server's windows."""
    results, _ = analyzer.analyze_clip(audio, "robustness")
    scores = [r.voice_score for r in results if r.voice_score is not None]
    return float(np.mean(scores)) if scores else None


def streaming_score(analyzer, audio: np.ndarray, hop: float) -> float | None:
    """The clip as a live call: the mean of the voice updates sent every `hop` seconds."""
    step = int(round(hop * SR))
    scores = []
    for end in range(step, len(audio) + 1, step):
        judged = analyzer.voice_now(audio[:end])
        if judged is not None and judged[0] is not None:
            scores.append(judged[0])
    return float(np.mean(scores)) if scores else None


def eers(clips: list[dict], scores: list[float | None]) -> dict:
    """EER over all scored clips, and for each synthetic family against all genuine clips."""
    from ml.evaluate import eer

    kept = [(c["label"], c["family"], s) for c, s in zip(clips, scores) if s is not None]
    y = np.array([k[0] for k in kept])
    fam = np.array([k[1] for k in kept])
    s = np.array([k[2] for k in kept])
    out = {"all": eer(y, s)[0]}
    for name in sorted(set(fam[y == 1])):
        pick = (y == 0) | (fam == name)
        out[name] = eer(y[pick], s[pick])[0]
    return {"eer": {k: round(v, 4) for k, v in out.items()}, "unscored": len(clips) - len(kept)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=BENCH / "results")
    ap.add_argument("--limit", type=int, default=0, help="score this many clips, half of each kind; writes nothing")
    ap.add_argument("--save-baseline", action="store_true", help="record this run's clean EERs as the Phase 1 baseline")
    args = ap.parse_args(argv)
    if args.limit and args.save_baseline:
        ap.error("--save-baseline needs the whole set (no --limit)")

    from backend.config import Settings
    from backend.pipeline import Analyzer
    from ml.anti_spoof import VoiceDetector
    from ml.channel import CONDITIONS
    from ml.evaluate import _NoTranscriber
    from ml.vad import speech_seconds

    settings = Settings()
    detector = VoiceDetector(settings)
    detector.preload()
    if detector.status()["state"] != "ready":
        print(f"Voice model unavailable: {detector.status()['error']}", file=sys.stderr)
        return 1
    analyzer = Analyzer(settings, _NoTranscriber(), detector, speech_seconds if settings.vad_enabled else None)
    clips = load_clips(args.limit)
    families: dict[str, int] = {}
    for c in clips:
        families[c["family"]] = families.get(c["family"], 0) + 1
    print(f"{len(clips)} clips: {json.dumps(families)}")

    report: dict = {"model": Path(settings.antispoof_onnx).name, "date": date.today().isoformat(),
                    "clips": families, "conditions": {}}
    for name, condition in CONDITIONS.items():
        started = time.time()
        result = eers(clips, [window_score(analyzer, condition(c["audio"])) for c in clips])
        report["conditions"][name] = result
        print(f"{name:12s} EER {result['eer']['all']:6.2%}   unscored {result['unscored']:3d}   "
              f"({time.time() - started:.0f} s)")

    clean = report["conditions"]["clean"]["eer"]["all"]
    worst = max(TARGET_CONDITIONS, key=lambda n: report["conditions"][n]["eer"]["all"])
    worst_eer = report["conditions"][worst]["eer"]["all"]
    report["phone_line"] = {
        "clean_eer": clean, "worst_condition": worst, "worst_eer": worst_eer,
        "ratio": round(worst_eer / clean, 2) if clean else None, "target_ratio": TARGET_RATIO,
        "pass": worst_eer <= TARGET_RATIO * clean,
    }

    started = time.time()
    hop = settings.voice_update_seconds or 0.5
    stream = eers(clips, [streaming_score(analyzer, c["audio"], hop) for c in clips])
    window_eer = report["conditions"]["clean"]["eer"]
    gaps = {k: round(stream["eer"][k] - window_eer[k], 4) for k in window_eer if k in stream["eer"]}
    report["phase1_streaming"] = {
        "update_seconds": hop, "window_eer": window_eer, "streaming_eer": stream["eer"],
        "unscored": stream["unscored"], "change": gaps, "max_change": MAX_DRIFT,
        "pass": all(abs(g) <= MAX_DRIFT for g in gaps.values()),
    }
    print(f"streaming    EER {stream['eer']['all']:6.2%}   ({time.time() - started:.0f} s)")

    baseline_path = args.out / "phase1_baseline.json"
    if args.save_baseline:
        args.out.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps({"model": report["model"], "date": report["date"], "eer": window_eer},
                                            indent=1), encoding="utf-8")
    if baseline_path.is_file() and not args.limit:
        base = json.loads(baseline_path.read_text(encoding="utf-8"))
        rise = {k: round(window_eer[k] - v, 4) for k, v in base["eer"].items() if k in window_eer}
        report["phase1_baseline"] = {"baseline_date": base["date"], "baseline_model": base["model"],
                                     "baseline_eer": base["eer"], "rise": rise, "max_rise": MAX_DRIFT,
                                     "pass": all(r <= MAX_DRIFT for r in rise.values())}

    checks = {k: report[k]["pass"] for k in ("phone_line", "phase1_streaming", "phase1_baseline") if k in report}
    report["pass"] = all(checks.values())
    if not args.limit:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "robustness.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    p = report["phone_line"]
    print(f"\nWorst phone line: {p['worst_condition']} {p['worst_eer']:.2%} vs clean {p['clean_eer']:.2%} "
          f"(ratio {p['ratio']}, target <= {TARGET_RATIO})")
    for name, ok in checks.items():
        print(f"{name:17s} {'pass' if ok else 'FAIL'}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
