"""Locate the synthetic stretches of the partial fakes (benchmark/vgsc25/partial_fakes) at 0.5 s resolution.

    python scripts/eval_partial_fakes.py                        # -> benchmark/vgsc25/results/localisation.json
    python scripts/eval_partial_fakes.py --window 1 2 4         # compare scoring windows (the first is the main one)
    DATABASE_URL=mysql://... python scripts/eval_partial_fakes.py --save   # also store the run in vg_eval_runs

Every 0.5 s frame gets the voice model's score of the `--window` seconds of audio centred on it
(moved inside the clip at its edges). A frame is called synthetic at VOICE_THRESHOLD, the server's
own cut-off, so nothing is tuned on this set; the best threshold is given beside it as an upper
bound. Precision, recall and F1 are over all frames of all 130 clips (100 partial fakes, 30 genuine).
--save needs the set in the database first: python scripts/load_scam_benchmark.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BENCH = ROOT / "benchmark" / "vgsc25"
SR = 16000


def frame_windows(n_samples: int, n_frames: int, frame: float, window: float) -> list[tuple[int, int]]:
    """(start, end) of the audio scored for each frame: `window` s centred on it, kept inside the clip."""
    w = int(round(window * SR))
    out = []
    for i in range(n_frames):
        centre = int(round((i + 0.5) * frame * SR))
        start = min(max(0, centre - w // 2), max(0, n_samples - w))
        out.append((start, min(n_samples, start + w)))
    return out


def counts(labels: str, pred: str) -> tuple[int, int, int, int]:
    """(tp, fp, fn, tn) of two '0'/'1' frame strings."""
    pairs = list(zip(labels, pred))
    return (pairs.count(("1", "1")), pairs.count(("0", "1")), pairs.count(("1", "0")), pairs.count(("0", "0")))


def f1(tp: int, fp: int, fn: int) -> float:
    return 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 1.0      # nothing to find, nothing claimed: perfect


def best_threshold(labels: np.ndarray, scores: np.ndarray) -> tuple[float, float]:
    """The threshold with the highest frame F1 on this set (optimistic), and that F1."""
    cuts = np.unique(np.round(scores, 3))
    pred = scores[None, :] >= cuts[:, None]
    tp = (pred & labels[None, :]).sum(1)
    fp = (pred & ~labels[None, :]).sum(1)
    fn = (~pred & labels[None, :]).sum(1)
    f = 2 * tp / np.maximum(2 * tp + fp + fn, 1)
    i = int(np.argmax(f))
    return float(cuts[i]), float(f[i])


def run(detector, manifest: dict, window: float, threshold: float) -> dict:
    import soundfile as sf

    frame = float(manifest["frame_seconds"])
    clips = []
    for clip in manifest["clips"]:
        audio, sr = sf.read(str(BENCH / "partial_fakes" / clip["file"]), dtype="float32")
        if sr != SR:
            raise SystemExit(f"{clip['file']} is {sr} Hz, expected {SR}")
        labels = clip["frame_labels"]
        scores = []
        for start, end in frame_windows(len(audio), len(labels), frame, window):
            s = detector.score(audio[start:end])
            if s is None:
                raise SystemExit(f"The voice model could not score {clip['name']}")
            scores.append(round(float(s), 4))
        pred = "".join("1" if s >= threshold else "0" for s in scores)
        tp, fp, fn, tn = counts(labels, pred)
        clips.append({"name": clip["name"], "engine": clip.get("fake_engine"), "labels": labels, "pred": pred,
                      "scores": scores, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "f1": round(f1(tp, fp, fn), 4)})
    tp, fp, fn, tn = (sum(c[k] for c in clips) for k in ("tp", "fp", "fn", "tn"))
    y = np.array([ch == "1" for c in clips for ch in c["labels"]])
    s = np.array([x for c in clips for x in c["scores"]])
    best_t, best_f = best_threshold(y, s)
    genuine = [c for c in clips if not c["engine"]]
    return {
        "window_seconds": window, "threshold": threshold, "frames": len(y), "synthetic_frames": int(y.sum()),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0, "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
        "f1": round(f1(tp, fp, fn), 4), "best_threshold": best_t, "best_f1": round(best_f, 4),
        "genuine_clips_with_a_false_alarm": sum(c["fp"] > 0 for c in genuine), "genuine_clips": len(genuine),
        "clips": clips,
    }


def save(result: dict, manifest: dict, model: str, database_url: str | None) -> int:
    from backend.storage.evaluation import dataset_clips, save_run
    from scripts.load_partial_fakes import connect

    conn = connect(database_url)
    try:
        dataset_id, _, rows = dataset_clips(conn, manifest["dataset"])
        ids = {r["clip_name"]: r["clip_id"] for r in rows}
        missing = [c["name"] for c in result["clips"] if c["name"] not in ids]
        if missing:
            raise SystemExit(f"{len(missing)} clips are not in the database, e.g. {missing[0]}; reload the set")
        run_info = {"model_name": model, "window_seconds": result["window_seconds"], "threshold": result["threshold"],
                    "precision": result["precision"], "recall": result["recall"], "f1": result["f1"],
                    "best_threshold": result["best_threshold"], "best_f1": result["best_f1"],
                    "notes": "scripts/eval_partial_fakes.py: frame centred windows, server VOICE_THRESHOLD"}
        predictions = [{"clip_id": ids[c["name"]], "scores": c["scores"], "pred": c["pred"], "tp": c["tp"],
                        "fp": c["fp"], "fn": c["fn"], "tn": c["tn"], "f1": c["f1"]} for c in result["clips"]]
        return save_run(conn, dataset_id, run_info, predictions)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", type=float, nargs="+", default=[2.0], help="seconds of audio scored per frame")
    ap.add_argument("--out", type=Path, default=BENCH / "results")
    ap.add_argument("--save", action="store_true", help="store the main run in the database (vg_eval_runs)")
    ap.add_argument("--database-url", help="default: $DATABASE_URL")
    args = ap.parse_args(argv)

    from backend.config import Settings
    from ml.anti_spoof import VoiceDetector

    settings = Settings()
    detector = VoiceDetector(settings)
    detector.preload()
    if detector.status()["state"] != "ready":
        print(f"Voice model unavailable: {detector.status()['error']}", file=sys.stderr)
        return 1
    manifest = json.loads((BENCH / "partial_fakes" / "manifest.json").read_text(encoding="utf-8"))
    model = Path(settings.antispoof_onnx).name
    runs = []
    for window in args.window:
        result = run(detector, manifest, window, settings.voice_threshold)
        runs.append(result)
        print(f"window {window:g} s: precision {result['precision']:.3f}  recall {result['recall']:.3f}  "
              f"F1 {result['f1']:.3f}   (best threshold {result['best_threshold']:.3f}: F1 {result['best_f1']:.3f})")
    report = {"dataset": manifest["dataset"], "model": model, "frame_seconds": manifest["frame_seconds"],
              "main": {k: v for k, v in runs[0].items() if k != "clips"},
              "other_windows": [{k: v for k, v in r.items() if k != "clips"} for r in runs[1:]],
              "clips": runs[0]["clips"]}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "localisation.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    if args.save:
        print(f"Saved as run {save(runs[0], manifest, model, args.database_url)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
