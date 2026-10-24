"""Score the backend on the VGSC-25 scam-call test set (benchmark/vgsc25).

    python scripts/eval_scam_benchmark.py text                 # scam rules + verdict, no models needed
    python scripts/eval_scam_benchmark.py voice                # voice model on the rendered audio
    python scripts/eval_scam_benchmark.py full                 # whole pipeline: speech-to-text, rules, voice
    python scripts/eval_scam_benchmark.py text --out reports/vgsc25

text   Each call's transcript is cut into windows of about 4 s of speech (WORDS_PER_WINDOW words)
       and fed through the server's own scoring: the scam rules over the last CONTEXT_WINDOWS
       windows, then VerdictTracker. So it measures the call-level verdict the page would show
       with perfect speech-to-text.
voice  Every rendered call (audio/manifest.json) through the voice model with the server's window
       rule (VOICE_MIN_SHARE of windows at VOICE_THRESHOLD, or one at VOICE_STRONG).
full   Analyzer.analyze_clip on every rendered call, as an uploaded recording would be. Needs the
       speech-to-text model and the voice model, like the server.

Metrics, with "scam" as the positive class: precision, recall, F1, accuracy, false-alarm rate on
the genuine calls, recall per scam type and language, and how far into the call the scam verdict
first appears. Results go to <out>/<mode>.json and are printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BENCH = ROOT / "benchmark" / "vgsc25"
WORDS_PER_WINDOW = 10            # about 4 s of speech at 2.5 words a second
SECONDS_PER_WORD = 0.4


def load_calls(path: Path = BENCH / "calls.jsonl") -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def windows_of(call: dict) -> list[str]:
    """The call as speech-to-text would hand it over: about 4 s of words at a time, never across turns."""
    out = []
    for turn in call["turns"]:
        words = turn["text"].split()
        for i in range(0, len(words), WORDS_PER_WINDOW):
            out.append(" ".join(words[i:i + WORDS_PER_WINDOW]))
    return out


def text_verdict(call: dict, settings) -> dict:
    from backend.risk_engine import risk_level
    from backend.scam_rules import score_text
    from backend.schemas import AnalysisResult
    from backend.verdict import VerdictTracker

    tracker = VerdictTracker(settings)
    history: deque[str] = deque(maxlen=settings.context_windows - 1)
    t, first_scam = 0.0, None
    for i, text in enumerate(windows_of(call)):
        duration = max(1.0, len(text.split()) * SECONDS_PER_WORD)
        t += duration
        score, reasons = score_text(" ".join([*history, text]))
        history.append(text)
        tracker.update(AnalysisResult(
            session_id=call["id"], timestamp=t, window_index=i, duration=duration, speech_seconds=duration,
            transcript=text, transcript_status="ok", language=None, scam_score=score, risk=risk_level(score),
            reasons=reasons, voice_score=None, voice_status="unavailable", voice_flag="unavailable", processing_ms=0))
        if first_scam is None and tracker.snapshot().verdict == "scam":
            first_scam = t
    final = tracker.snapshot(final=True)
    return {"verdict": final.verdict, "scam_score": round(final.scam_score, 3), "reasons": final.reasons,
            "seconds": round(t, 1), "first_scam_at": first_scam}


def summarize(rows: list[dict]) -> dict:
    tp = sum(r["label"] == 1 and r["pred"] == 1 for r in rows)
    fp = sum(r["label"] == 0 and r["pred"] == 1 for r in rows)
    fn = sum(r["label"] == 1 and r["pred"] == 0 for r in rows)
    tn = sum(r["label"] == 0 and r["pred"] == 0 for r in rows)
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    by = lambda key: {k: {"caught": sum(r["pred"] for r in g), "total": len(g)}
                      for k, g in sorted(_group(rows, key).items())}
    scams = [r for r in rows if r["label"] == 1]
    genuine = [r for r in rows if r["label"] == 0]
    times = sorted(r["first_scam_at"] for r in scams if r.get("first_scam_at") is not None)
    return {
        "calls": len(rows), "scam_calls": len(scams), "genuine_calls": len(genuine),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(p, 3), "recall": round(rc, 3), "f1": round(2 * p * rc / (p + rc), 3) if p + rc else 0.0,
        "accuracy": round((tp + tn) / len(rows), 3) if rows else 0.0,
        "false_alarm_rate": round(fp / len(genuine), 3) if genuine else 0.0,
        "median_seconds_to_scam_verdict": times[len(times) // 2] if times else None,
        "recall_by_scam_type": by("scam_type") if scams else {},
        "by_language": {k: {"correct": sum(r["pred"] == r["label"] for r in g), "total": len(g)}
                        for k, g in sorted(_group(rows, "language").items())},
        "errors": [{k: r[k] for k in ("id", "label", "verdict", "scam_score", "reasons")} for r in rows if r["pred"] != r["label"]],
    }


def _group(rows, key):
    out = defaultdict(list)
    for r in rows:
        if key == "scam_type" and r["label"] != 1:
            continue
        out[r[key]].append(r)
    return out


def run_text(settings) -> dict:
    rows = []
    for call in load_calls():
        res = text_verdict(call, settings)
        rows.append({**{k: call[k] for k in ("id", "label", "scam_type", "language", "incident_id")}, **res,
                     "pred": int(res["verdict"] == "scam")})
    return {"mode": "text", "summary": summarize(rows), "calls": rows}


def run_voice(settings) -> dict:
    import soundfile as sf
    from ml.anti_spoof import load_onnx

    manifest = json.loads((BENCH / "audio" / "manifest.json").read_text(encoding="utf-8"))
    det = load_onnx(ROOT / "ml/models/antideepfake-w2v-small.onnx", spoof_index=0)
    size = int(settings.window_seconds * 16000)
    rows = []
    for clip in manifest["clips"]:
        audio, sr = sf.read(str(BENCH / "audio" / clip["file"]), dtype="float32")
        scores = [s for s in (det.score(audio[i:i + size]) for i in range(0, len(audio) - 16000, size)) if s is not None]
        flagged = sum(s >= settings.voice_threshold for s in scores)
        ai = bool(scores) and (any(s >= settings.voice_strong for s in scores)
                               or (flagged >= min(2, len(scores)) and flagged / len(scores) >= settings.voice_min_share))
        rows.append({"id": clip["id"], "call_id": clip["call_id"], "condition": clip["condition"],
                     "voice_label": clip["voice_label"], "windows": len(scores), "flagged": flagged,
                     "ai_voice": ai, "scores": [round(s, 3) for s in scores]})
    by = defaultdict(lambda: {"flagged_ai": 0, "total": 0})
    for r in rows:
        k = f"{r['voice_label']}/{r['condition']}"
        by[k]["total"] += 1
        by[k]["flagged_ai"] += int(r["ai_voice"])
    return {"mode": "voice", "summary": {"clips": len(rows), "flagged_ai_by_label_and_condition": dict(sorted(by.items()))},
            "clips": rows}


def run_full(settings) -> dict:
    import soundfile as sf
    from backend.pipeline import Analyzer

    manifest = json.loads((BENCH / "audio" / "manifest.json").read_text(encoding="utf-8"))
    calls = {c["id"]: c for c in load_calls()}
    analyzer = Analyzer.from_settings(settings)
    analyzer.preload()
    rows = []
    for clip in manifest["clips"]:
        audio, _ = sf.read(str(BENCH / "audio" / clip["file"]), dtype="float32")
        results, verdict = analyzer.analyze_clip(audio, clip["id"])
        first = next((r.timestamp for r in results if r.verdict and r.verdict.verdict == "scam"), None)
        call = calls[clip["call_id"]]
        rows.append({"id": clip["id"], "label": call["label"], "scam_type": call["scam_type"], "language": call["language"],
                     "condition": clip["condition"], "verdict": verdict.verdict, "scam_score": round(verdict.scam_score, 3),
                     "reasons": verdict.reasons, "ai_voice": verdict.ai_voice, "first_scam_at": first,
                     "pred": int(verdict.verdict == "scam"),
                     "transcript": " | ".join(r.transcript for r in results if r.transcript)})
    by_condition = {k: summarize(g) for k, g in sorted(_group(rows, "condition").items())}
    for s in by_condition.values():
        s.pop("errors")
    return {"mode": "full", "summary": summarize(rows), "by_condition": by_condition, "calls": rows}


def main(argv: list[str] | None = None) -> int:
    from backend.config import Settings

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["text", "voice", "full"])
    ap.add_argument("--out", type=Path, default=BENCH / "results")
    args = ap.parse_args(argv)
    settings = Settings()
    result = {"text": run_text, "voice": run_voice, "full": run_full}[args.mode](settings)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.mode}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["summary"], indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
