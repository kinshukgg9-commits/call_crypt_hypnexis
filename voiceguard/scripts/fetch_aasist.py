"""Download the pretrained AASIST anti-spoofing model into ml/weights/.

Source: https://github.com/clovaai/aasist (MIT License, Copyright (c) 2021-present NAVER Corp.),
pinned to one commit, with every file checked against a SHA-256 hash.

    python scripts/fetch_aasist.py            # skip files that are already present and valid
    python scripts/fetch_aasist.py --force    # download again
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

COMMIT = "a04c9863f63d44471dde8a6abcb3b082b07cd1d1"
BASE = f"https://raw.githubusercontent.com/clovaai/aasist/{COMMIT}"
FILES = {  # local name: (path in repo, sha256)
    "AASIST.py": ("models/AASIST.py", "9e0d3e80937dd0577beea7883098465a479da23a198ebc0d712abcc59b0bec50"),
    "AASIST.conf": ("config/AASIST.conf", "c25023331685027cce90e1b9a0d2df10aa04b2a27d9b27d5afa36e6815b0fe76"),
    "AASIST.pth": ("models/weights/AASIST.pth", "51d2d9cf0738172f61e2a384ec50a54a55363240f67c971ed55a92435bc1a1c0"),
    "LICENSE-AASIST": ("LICENSE", "da2e79b8592d166ef505224300968b80ebe1e4c217c43b94a5ec627d81cd4142"),
}
DEFAULT_DEST = Path(__file__).resolve().parent.parent / "ml" / "weights"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(dest: Path, force: bool = False) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name, (repo_path, digest) in FILES.items():
        target = dest / name
        if not force and target.is_file() and sha256(target) == digest:
            print(f"ok      {name} (already present)")
            continue
        url = f"{BASE}/{repo_path}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise RuntimeError(f"{name}: SHA-256 mismatch (expected {digest}, got {actual}); refusing to save")
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        print(f"fetched {name} ({len(data):,} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Download the pretrained AASIST model.")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    try:
        fetch(args.dest, args.force)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"AASIST ready in {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
