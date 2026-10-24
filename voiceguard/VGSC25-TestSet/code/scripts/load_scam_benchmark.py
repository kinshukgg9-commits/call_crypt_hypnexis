"""Load the VGSC-25 scam-call test set (benchmark/vgsc25) into MySQL.

    python scripts/load_scam_benchmark.py --dry-run          # checks files and prints what would load
    DATABASE_URL=mysql://user:pass@host:3306/voiceguard python scripts/load_scam_benchmark.py

Loads the incidents and their sources, the call scripts, the audio renderings (file path and
SHA-256; the audio stays in files) and the 130 partial-fake clips (as dataset partial-fakes-v2,
through scripts/load_partial_fakes.py). Creates the tables if needed (migration 3). Loading again
replaces the same rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.storage.evaluation import load_manifest, load_scam_benchmark  # noqa: E402
from backend.storage.schema import migrate  # noqa: E402
from scripts.load_partial_fakes import connect  # noqa: E402

BENCH = ROOT / "benchmark" / "vgsc25"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check(folder: Path, manifest: dict) -> None:
    for clip in manifest["clips"]:
        path = folder / clip["file"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != clip["sha256"]:
            raise SystemExit(f"{path} is missing or differs from the manifest")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", help="default: $DATABASE_URL")
    ap.add_argument("--dry-run", action="store_true", help="check the files and print counts; touch no database")
    args = ap.parse_args(argv)
    incidents = read_jsonl(BENCH / "incidents.jsonl")
    calls = read_jsonl(BENCH / "calls.jsonl")
    audio = json.loads((BENCH / "audio" / "manifest.json").read_text(encoding="utf-8"))
    pf = json.loads((BENCH / "partial_fakes" / "manifest.json").read_text(encoding="utf-8"))
    check(BENCH / "audio", audio)
    check(BENCH / "partial_fakes", pf)
    unknown = {c["incident_id"] for c in calls if c.get("incident_id")} - {i["id"] for i in incidents}
    if unknown:
        raise SystemExit(f"calls refer to unknown incidents: {sorted(unknown)}")
    counts = {"incidents": len(incidents), "sources": sum(len(i["sources"]) for i in incidents), "calls": len(calls),
              "call_clips": len(audio["clips"]), "partial_fake_clips": len(pf["clips"])}
    if args.dry_run:
        print("Files check out. Would load:", json.dumps(counts))
        return 0
    conn = connect(args.database_url)
    try:
        migrate(conn)
        loaded = load_scam_benchmark(conn, incidents, calls, audio)
        pf_id, n = load_manifest(conn, pf)
    finally:
        conn.close()
    print("Loaded:", json.dumps({**loaded, "partial_fake_clips": n, "partial_fake_dataset_id": pf_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
