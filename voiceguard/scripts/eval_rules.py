"""Report precision and recall of the scam rules on a labeled set.

    python scripts/eval_rules.py                     # tests/data/scam_lines.tsv
    python scripts/eval_rules.py my_lines.tsv        # label<TAB>text, 1 = scam

A line counts as flagged when it alone would reach the scam verdict.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import Settings  # noqa: E402
from backend.scam_rules import score_text  # noqa: E402

THRESHOLD = Settings().verdict_scam_threshold


def main(path: Path) -> int:
    tp = fp = fn = tn = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        label, text = line.split("\t", 1)
        score, reasons = score_text(text)
        flagged, scam = score >= THRESHOLD, label.strip() == "1"
        tp += flagged and scam
        fp += flagged and not scam
        fn += (not flagged) and scam
        tn += (not flagged) and not scam
        if flagged != scam:
            kind = "FALSE POSITIVE" if flagged else "MISSED SCAM   "
            print(f"{kind} {score:.2f} {reasons}  {text}")
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    print(f"\nlines={tp + fp + fn + tn}  TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"precision={precision:.2f}  recall={recall:.2f}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "data" / "scam_lines.tsv"))
