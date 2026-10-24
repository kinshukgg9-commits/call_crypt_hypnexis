"""Check that the VGSC-25 test set shares nothing with what the backend was tuned or trained on.

    python scripts/check_benchmark_overlap.py
    python scripts/check_benchmark_overlap.py --train-cache data/voice/cache     # after `train prep`

Text: every line of benchmark/vgsc25/calls.jsonl against the lines the scam rules were tuned on
(tests/data/scam_lines.tsv, plus --text-file). A line overlaps when it is the same after
normalising, or shares OVERLAP_JACCARD of its word trigrams with a tuning line.

Audio (needs --train-cache, the folder `python -m ml.train.train prep` writes): every rendered test
clip and every partial-fake clip against the training cache. A clip overlaps when its trimmed
16 kHz PCM hashes the same as a training clip, when a training clip's source is one of the test
set's generators or speakers, or when a file stem matches.
Exits 1 if anything overlaps, so it can gate a release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmark" / "vgsc25"
OVERLAP_JACCARD = 0.5


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def trigrams(text: str) -> set[tuple[str, ...]]:
    w = norm(text).split()
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)} or {tuple(w)}


def text_overlap(extra: list[Path]) -> list[dict]:
    tuning = []
    for path in [ROOT / "tests/data/scam_lines.tsv", *extra]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                tuning.append(line.split("\t", 1)[-1])
    tuned = [(t, norm(t), trigrams(t)) for t in tuning]
    hits = []
    for line in (BENCH / "calls.jsonl").read_text(encoding="utf-8").splitlines():
        call = json.loads(line)
        for i, turn in enumerate(call["turns"]):
            n, g = norm(turn["text"]), trigrams(turn["text"])
            for t, tn, tg in tuned:
                j = len(g & tg) / len(g | tg)
                if n == tn or j >= OVERLAP_JACCARD:
                    hits.append({"call": call["id"], "turn": i, "text": turn["text"], "tuning_line": t, "jaccard": round(j, 2)})
    return hits


def pcm_hash(path: Path) -> str:
    import numpy as np
    import soundfile as sf

    sys.path.insert(0, str(ROOT))
    from ml.train.train import trim

    audio, sr = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(1)
    if sr != 16000:
        from ml.audio_preprocess import resample
        audio = resample(audio, sr, 16000)
    pcm = trim((np.clip(audio, -1, 1) * 32767).astype(np.int16))
    return hashlib.sha256(pcm.tobytes()).hexdigest()


def audio_overlap(cache: Path) -> list[dict]:
    import numpy as np

    index = json.loads((cache / "index.json").read_text(encoding="utf-8"))
    pcm = np.load(cache / "audio.npy", mmap_mode="r") if (cache / "audio.npy").exists() else None
    train_hashes = {}
    if pcm is not None:
        for e in index:
            seg = np.asarray(pcm[e["start"]:e["start"] + e["n"]])     # the clean copy; the codec copy follows it
            train_hashes[hashlib.sha256(seg.tobytes()).hexdigest()] = e.get("name")
    train_sources = {str(e.get("source", "")).lower() for e in index}
    train_stems = {Path(str(e.get("name", ""))).stem for e in index}

    clips = []
    audio_manifest = BENCH / "audio" / "manifest.json"
    if audio_manifest.exists():
        m = json.loads(audio_manifest.read_text(encoding="utf-8"))
        clips += [(BENCH / "audio" / c["file"], c.get("generators", []) + c.get("speakers", [])) for c in m["clips"]]
    pf = BENCH / "partial_fakes" / "manifest.json"
    if pf.exists():
        m = json.loads(pf.read_text(encoding="utf-8"))
        for c in m["clips"]:
            gens = sorted({s["source"].split(":")[0] for s in c["segments"] if s["label"] == "fake"})
            clips.append((pf.parent / c["file"], gens + [c["real_speaker"]]))
    hits = []
    for path, origins in clips:
        reasons = []
        h = pcm_hash(path)
        if h in train_hashes:
            reasons.append(f"same audio as training clip {train_hashes[h]}")
        for o in origins:
            if o.lower() in train_sources or o.split("-")[0].lower() in train_sources:
                reasons.append(f"training data has source '{o}'")
        if path.stem in train_stems:
            reasons.append("file name matches a training clip")
        if reasons:
            hits.append({"clip": str(path.relative_to(ROOT)), "reasons": reasons})
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train-cache", type=Path)
    ap.add_argument("--text-file", type=Path, action="append", default=[], help="more tuning lines (label<TAB>text)")
    args = ap.parse_args(argv)
    text = text_overlap(args.text_file)
    report = {"text_overlaps": text, "audio_checked": bool(args.train_cache)}
    if args.train_cache:
        report["audio_overlaps"] = audio_overlap(args.train_cache)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    bad = text or report.get("audio_overlaps")
    print("OVERLAP FOUND" if bad else "No overlap found." + ("" if args.train_cache else " Audio not checked: pass --train-cache."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
