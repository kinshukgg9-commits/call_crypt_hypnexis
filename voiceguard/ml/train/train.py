"""Fine-tune the voice detector on newer AI voices and harder listening conditions.

    python -m ml.train.train prep            # decode data/voice/raw/{real,fake}/<source>/* into a cache
    python -m ml.train.train fit             # train on the GPU -> data/voice/run/detector.pt
    python -m ml.train.train blend           # average with the public weights -> run/blend50.pt
    python scripts/build_voice_model.py --weights data/voice/run/blend50.pt      # -> ml/models/*.onnx

The starting point is the public AntiDeepfake checkpoint (see scripts/build_voice_model.py). Only
the transformer and the output layer are trained; the convolutional front end stays as it was.
Every clip is heard clean, over a phone line, through a loudspeaker and a room, or in noise
(ml/train/augment.py), and half the time after a real codec (ml/train/audio.py).

Some voices, generators and recording sources are never trained on (TEST_* below), so the numbers
printed during training say how the model does on voices it has not met. Fine-tuning alone made the
model worse on generators it had no samples of; averaging its weights with the public ones keeps
most of the gain (phone lines, rooms, the trained voices) without that loss, so that is what ships.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

SR = 16000
EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
MAX_SECONDS, MIN_SECONDS = 20.0, 2.0
CODEC_COPIES = True                     # prep --fast turns this off for a trial run

# Never trained on: whole generators, whole recording sources, and single voices of the others.
TEST_FAKE = {"cartesia", "cosyvoice2", "el_ru", "openai_shanghai", "orpheus", "gemini_fa"}
TEST_REAL = {"earnings", "ami_sdm", "ami_ihm", "peoples", "minds_au", "inen"}
TEST_VOICES = {
    "en-US-AndrewMultilingualNeural", "en-US-EmmaMultilingualNeural", "en-IN-PrabhatNeural", "hi-IN-MadhurNeural",
    "en-GB-RyanNeural", "en-US-MichelleNeural", "en-AU-NatashaNeural", "fr-FR-VivienneMultilingualNeural",
    "af_nova", "am_onyx", "bm_fable", "hf_beta", "Kannada", "Bodo",
}


# ---------------------------------------------------------------------------------------------------
# prep: raw files -> one int16 array + an index
# ---------------------------------------------------------------------------------------------------
def trim(pcm: np.ndarray) -> np.ndarray:
    """Drop the silence before and after the speech (generated clips start and end in dead silence)."""
    frame = 320
    n = len(pcm) // frame
    if n < 10:
        return pcm
    energy = (pcm[: n * frame].astype(np.float32).reshape(n, frame) ** 2).mean(1)
    loud = np.nonzero(energy > energy.max() * 1e-3)[0]
    if not len(loud):
        return pcm[:0]
    return pcm[max(loud[0] - 3, 0) * frame: (loud[-1] + 4) * frame]


def load_clip(job):
    from ml.train.audio import decode, random_codec

    path, label, source = job
    try:
        pcm = trim(decode(path))[: int(MAX_SECONDS * SR)]
    except Exception:
        return None
    if len(pcm) < MIN_SECONDS * SR or np.abs(pcm).max() < 40:
        return None
    meta = path.with_suffix(".json")
    voice = json.loads(meta.read_text(encoding="utf-8")).get("voice") if meta.is_file() else None
    rng = random.Random(path.name)
    return {"label": label, "source": source, "voice": voice, "name": path.name}, pcm, random_codec(pcm, rng) if CODEC_COPIES else pcm


def prep(data: Path) -> None:
    jobs = [(p, label, p.parent.name) for label in ("real", "fake") for p in sorted((data / "raw" / label).glob("*/*"))
            if p.suffix.lower() in EXT]
    print(f"{len(jobs)} files", flush=True)
    index, chunks, at, t0 = [], [], 0, time.time()
    with ThreadPoolExecutor(12) as pool:
        for k, result in enumerate(pool.map(load_clip, jobs)):
            if result is None:
                continue
            entry, pcm, coded = result
            entry.update(start=at, n=len(pcm))
            index.append(entry)
            chunks += [pcm, coded]
            at += 2 * len(pcm)
            if k % 1000 == 999:
                print(f"  {k + 1} decoded, {time.time() - t0:.0f} s", flush=True)
    cache = data / "cache"
    cache.mkdir(exist_ok=True)
    np.save(cache / "audio.npy", np.concatenate(chunks))
    (cache / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    hours = {}
    for e in index:
        key = (e["label"], e["source"])
        hours[key] = hours.get(key, 0) + e["n"] / SR / 3600
    for (label, source), h in sorted(hours.items()):
        print(f"  {label:4s} {source:18s} {sum(1 for e in index if e['source'] == source and e['label'] == label):5d} clips {h:5.2f} h")
    print(f"cache: {len(index)} clips, {at / 2 / SR / 3600:.1f} h, {time.time() - t0:.0f} s")


# ---------------------------------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------------------------------
def which_split(e: dict) -> str:
    if e["source"] in (TEST_FAKE if e["label"] == "fake" else TEST_REAL) or e["voice"] in TEST_VOICES:
        return "test"
    return "val" if int(hashlib.md5(e["name"].encode()).hexdigest(), 16) % 12 == 0 else "train"


class Clips:
    def __init__(self, data: Path):
        self.audio = np.load(data / "cache" / "audio.npy")
        self.index = json.loads((data / "cache" / "index.json").read_text(encoding="utf-8"))
        for e in self.index:
            e["split"] = which_split(e)

    def pool(self, split: str, label: str) -> dict[str, list[dict]]:
        by_source: dict[str, list[dict]] = {}
        for e in self.index:
            if e["split"] == split and e["label"] == label:
                by_source.setdefault(e["source"], []).append(e)
        return by_source

    def crop(self, e: dict, length: int, rng: random.Random, coded: bool) -> np.ndarray:
        base = e["start"] + (e["n"] if coded else 0)
        if e["n"] <= length:
            clip = np.asarray(self.audio[base: base + e["n"]])
            return np.resize(np.roll(clip, rng.randrange(e["n"])), length)       # short clip: loop it
        best = None
        for _ in range(4):                                                      # prefer a crop with speech in it
            start = rng.randrange(e["n"] - length)
            clip = np.asarray(self.audio[base + start: base + start + length])
            if best is None or np.abs(clip).mean() > np.abs(best).mean():
                best = clip
            if np.abs(best).mean() > 150:
                break
        return best


def sampler(clips: Clips, split: str, batch: int, seed: int, lengths=(48000, 64000, 64000, 80000)):
    """Endless balanced batches: half real, half fake, every source heard about equally often."""
    rng = random.Random(seed)
    pools = {label: clips.pool(split, label) for label in ("real", "fake")}
    weights = {label: [min(len(v), 400) ** 0.5 for v in pools[label].values()] for label in pools}
    while True:
        length = rng.choice(lengths)
        xs, ys = [], []
        for i in range(batch):
            label = ("fake", "real")[i % 2]
            source = rng.choices(list(pools[label]), weights[label])[0]
            xs.append(clips.crop(rng.choice(pools[label][source]), length, rng, coded=rng.random() < 0.4))
            ys.append(i % 2)                                                    # model output 0 = fake, 1 = real
        yield np.stack(xs), np.array(ys)


def fixed_set(clips: Clips, split: str, per_class: int, seed: int):
    """The same crops every time, for measuring: (audio, labels, sources)."""
    rng = random.Random(seed)
    xs, ys, sources = [], [], []
    for y, label in enumerate(("fake", "real")):
        pools = clips.pool(split, label)
        entries = [e for v in pools.values() for e in rng.sample(v, min(len(v), max(8, per_class // max(len(pools), 1))))]
        for e in entries[:per_class] if len(entries) > per_class else entries:
            xs.append(clips.crop(e, 64000, rng, coded=False))
            ys.append(y)
            sources.append(e["source"])
    return np.stack(xs), np.array(ys), sources


# ---------------------------------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------------------------------
def forward(detector, wav, amp: bool = True):
    """The detector's forward pass with the convolutional front end frozen and in full precision."""
    import torch

    mean = wav.mean(-1, keepdim=True)
    wav = (wav - mean) / torch.sqrt(((wav - mean) ** 2).mean(-1, keepdim=True) + 1e-5)
    with torch.no_grad():                                           # a few clips at a time: this part is memory-hungry
        features = torch.cat([detector.model.feature_extractor(part[:, None]) for part in wav.split(8)]).transpose(1, 2)
    with torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp and wav.is_cuda):
        hidden = detector.model.encoder(detector.model.post_extract_proj(detector.model.layer_norm(features)))
        return detector.proj_fc(hidden.mean(1)).float()


def auc(scores: np.ndarray, fake: np.ndarray) -> float:
    order = scores.argsort()
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)
    pos, neg = fake.sum(), (~fake).sum()
    return float((ranks[fake].sum() - pos * (pos + 1) / 2) / max(pos * neg, 1))


def measure(detector, sets: dict, conditions: dict, device) -> dict:
    """AUC, AI clips caught and real clips wrongly flagged (score >= 0.5), per set and condition."""
    import torch

    out = {}
    detector.eval()
    for set_name, (xs, ys, _) in sets.items():
        for cond, aug in conditions.items():
            aug.g.manual_seed(1234)
            scores = []
            with torch.inference_mode():
                for i in range(0, len(xs), 48):
                    x = torch.from_numpy(xs[i: i + 48]).to(device).float()
                    scores.append(torch.softmax(forward(detector, aug(x)), -1)[:, 0].cpu().numpy())
            s, fake = np.concatenate(scores), ys == 0
            out[f"{set_name}/{cond}"] = (auc(s, fake), float((s[fake] >= 0.5).mean()), float((s[~fake] >= 0.5).mean()))
    return out


def show(tag: str, result: dict) -> None:
    print(f"{tag}")
    for key, (a, caught, false_alarm) in result.items():
        print(f"    {key:16s} AUC {a:.3f}   AI caught {caught * 100:5.1f}%   real wrongly flagged {false_alarm * 100:5.1f}%")
    sys.stdout.flush()


def fit(data: Path, steps: int, batch: int, lr: float, seed: int, rirs: Path | None) -> None:
    import torch
    import build_voice_model as base
    from ml.train.augment import Augmenter

    torch.manual_seed(seed)
    device = torch.device("cuda")
    torch.backends.cudnn.benchmark = True
    clips = Clips(data)
    counts = {}
    for e in clips.index:
        counts[(e["split"], e["label"])] = counts.get((e["split"], e["label"]), 0) + 1
    print("clips:", {f"{k[0]}/{k[1]}": v for k, v in sorted(counts.items())}, flush=True)

    measured = None
    if rirs and rirs.is_file():                                     # measured rooms: only ever used to test
        measured = torch.from_numpy(np.load(rirs)).float()
    conditions = {
        "clean": Augmenter(device, p_clean=1.0, p_phone=0.0, p_air=0.0, extras=False),
        "phone": Augmenter(device, p_clean=0.0, p_phone=1.0, p_air=0.0, extras=False),
        "room": Augmenter(device, rirs=measured, p_clean=0.0, p_phone=0.0, p_air=1.0, p_measured=1.0 if measured is not None else 0.0, extras=False),
    }
    sets = {"seen": fixed_set(clips, "val", 400, 1), "unseen": fixed_set(clips, "test", 700, 2)}
    print("measuring on", {k: (int((v[1] == 0).sum()), int((v[1] == 1).sum())) for k, v in sets.items()}, "(AI, real) crops")

    detector = base.load_detector(base.CKPT).to(device)
    show("before training", measure(detector, sets, conditions, device))

    for p in detector.model.feature_extractor.parameters():
        p.requires_grad_(False)
    params = [p for p in detector.parameters() if p.requires_grad]
    optimiser = torch.optim.AdamW(params, lr=lr, weight_decay=0.01)
    warmup = 300
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimiser, lambda s: min(1.0, (s + 1) / warmup) * (0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * min(s / steps, 1.0)))))
    average = copy.deepcopy(detector).eval()                         # a running average of the weights: steadier
    augment = Augmenter(device, seed=seed)
    stream = sampler(clips, "train", batch, seed)
    out = data / "run"
    out.mkdir(exist_ok=True)
    t0, running = time.time(), 0.0
    for step in range(1, steps + 1):
        detector.train()
        detector.model.feature_extractor.eval()
        x, y = next(stream)
        x = augment(torch.from_numpy(x).to(device).float())
        loss = torch.nn.functional.cross_entropy(forward(detector, x), torch.from_numpy(y).to(device), label_smoothing=0.05)
        optimiser.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        optimiser.step()
        schedule.step()
        decay = min(0.999, (1 + step) / (10 + step))
        with torch.no_grad():
            for a, p in zip(average.parameters(), detector.parameters()):
                a.lerp_(p, 1 - decay)
        running += loss.item()
        if step % 100 == 0:
            print(f"step {step:5d}  loss {running / 100:.4f}  {(time.time() - t0) / step:.2f} s/step", flush=True)
            running = 0.0
        if step % 1000 == 0 or step == steps:
            result = measure(average, sets, conditions, device)
            show(f"after {step} steps", result)
            torch.save({k: v.cpu() for k, v in average.state_dict().items()}, out / "detector.pt")
            (out / "metrics.json").write_text(json.dumps({"step": step, "result": result}, indent=1))
    print(f"saved {out / 'detector.pt'}  ({(time.time() - t0) / 60:.1f} min)")


def blend(data: Path, share: float) -> None:
    import torch
    import build_voice_model as base

    public = base.load_detector(base.CKPT).state_dict()
    tuned = torch.load(data / "run" / "detector.pt", map_location="cpu")
    out = data / "run" / f"blend{round(share * 100)}.pt"
    torch.save({k: (1 - share) * public[k] + share * tuned[k] for k in public}, out)
    print(f"saved {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("prep", "fit", "blend"))
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "voice")
    ap.add_argument("--steps", type=int, default=5000)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--rirs", type=Path, default=None, help=".npy of measured room responses, used only for testing")
    ap.add_argument("--fast", action="store_true", help="prep: skip the codec copies (trial runs)")
    ap.add_argument("--share", type=float, default=0.5, help="blend: how much of the fine-tuned weights to keep")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    if args.command == "prep":
        global CODEC_COPIES
        CODEC_COPIES = not args.fast
        prep(args.data)
    elif args.command == "blend":
        blend(args.data, args.share)
    else:
        fit(args.data, args.steps, args.batch, args.lr, args.seed, args.rirs or args.data / "rir" / "mit.npy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
