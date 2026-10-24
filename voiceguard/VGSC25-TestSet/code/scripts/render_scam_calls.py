"""Render the VGSC-25 call scripts (benchmark/vgsc25/calls.jsonl) as phone calls.

    python scripts/render_scam_calls.py                      # -> benchmark/vgsc25/audio/

Each turn is spoken by an offline stock voice (eSpeak NG, MBROLA, Festival, Flite, Pico; none
imitates a real person and none is in the voice model's training data), caller and callee in
different voices, with short pauses between turns. Every call is written clean and over the
phone-line conditions in CONDITIONS (ml/channel.py), as 16 kHz FLAC, with a manifest giving each
turn's start and end time.

The voices are synthetic, so every clip's voice label is "synthetic": these clips test the scam
check through speech-to-text, and the voice check on unseen generators. Real-voice scam calls
need consenting people to read the same scripts (see benchmark/vgsc25/RECORDING.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_partial_fakes import _synthesize, load, rms, trim_silence  # noqa: E402

BENCH = ROOT / "benchmark" / "vgsc25"
SR = 16000
CONDITIONS = ["clean", "g711_mulaw", "phone_worst"]
# Caller and callee voices by script language: (family, voice). Pairs are taken in turn.
VOICES = {
    "en": [("espeak-mbrola", "mb-us1"), ("flite", "slt"), ("festival-diphone", "kal_diphone"), ("pico", "en-GB"),
           ("espeak-mbrola", "mb-us3"), ("flite", "awb"), ("festival-hts", "cmu_us_slt_arctic_hts"), ("pico", "en-US"),
           ("espeak-ng", "en-gb+f2"), ("flite", "rms")],
    "hi-Latn": [("espeak-mbrola", "mb-en1"), ("espeak-ng", "en-us"), ("flite", "kal16"), ("espeak-mbrola", "mb-us2")],
    "hi": [("espeak-mbrola", "mb-in1"), ("espeak-mbrola", "mb-in2"), ("espeak-ng", "hi")],
}


def speak(family: str, voice: str, text: str, seed: int) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "t.wav")
        cmd = _synthesize(family, voice, text, out, random.Random(seed))
        subprocess.run(cmd, input=text.encode() if family.startswith("festival") else None,
                       check=True, capture_output=True, timeout=120)
        return trim_silence(load(Path(out)))


def render(job: tuple[dict, int]) -> list[dict]:
    from ml.channel import CONDITIONS as CHANNEL

    call, k = job
    pool = VOICES[call["language"]]
    voices = {"caller": pool[(2 * k) % len(pool)], "callee": pool[(2 * k + 1) % len(pool)]}
    rng = random.Random(f"vgsc25-{call['id']}")
    parts, turns, t = [np.zeros(int(0.3 * SR), np.float32)], [], 0.3
    for i, turn in enumerate(call["turns"]):
        family, voice = voices[turn["speaker"]]
        audio = speak(family, voice, turn["text"], rng.randint(0, 10**6))
        audio = audio * (0.1 / (rms(audio) or 1.0))                       # same loudness for every voice
        turns.append({"speaker": turn["speaker"], "start": round(t, 3), "end": round(t + len(audio) / SR, 3),
                      "text": turn["text"], "voice": f"{family}:{voice}"})
        gap = np.zeros(int(rng.uniform(0.4, 0.8) * SR), np.float32)
        parts += [audio.astype(np.float32), gap]
        t += (len(audio) + len(gap)) / SR
    clean = np.clip(np.concatenate(parts), -1, 1)
    out = []
    for cond in CONDITIONS:
        y = CHANNEL[cond](clean).astype(np.float32)
        name = f"{call['id']}_{cond}.flac"
        path = BENCH / "audio" / name
        sf.write(str(path), y, SR, subtype="PCM_16")
        out.append({"id": f"{call['id']}_{cond}", "call_id": call["id"], "condition": cond, "file": name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "duration": round(len(y) / SR, 2),
                    "label": call["label"], "voice_label": "synthetic",
                    "generators": sorted({v[0] for v in voices.values()}), "speakers": [],
                    "turns": turns})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    calls = [json.loads(line) for line in (BENCH / "calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    (BENCH / "audio").mkdir(parents=True, exist_ok=True)
    counters: dict[str, int] = {}
    jobs = []
    for call in calls:
        k = counters.get(call["language"], 0)
        counters[call["language"]] = k + 1
        jobs.append((call, k))
    clips = []
    with ProcessPoolExecutor(args.workers) as pool:
        for rendered in pool.map(render, jobs):
            clips += rendered
    manifest = {"dataset": "vgsc25-audio", "sample_rate": SR, "conditions": CONDITIONS,
                "description": "VGSC-25 call scripts spoken by offline stock voices, clean and over phone-line conditions",
                "clips": clips}
    (BENCH / "audio" / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    minutes = sum(c["duration"] for c in clips) / 60
    print(f"{len(clips)} clips, {minutes:.1f} minutes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
