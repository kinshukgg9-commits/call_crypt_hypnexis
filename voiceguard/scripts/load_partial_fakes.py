"""Load a partial-fakes manifest (e.g. benchmark/vgsc25/partial_fakes/manifest.json) into MySQL.

    DATABASE_URL=mysql://user:pass@host:3306/voiceguard python scripts/load_partial_fakes.py MANIFEST

Creates the evaluation tables if needed. Loading the same manifest again replaces its rows.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.storage.config import StorageSettings  # noqa: E402
from backend.storage.evaluation import load_manifest  # noqa: E402
from backend.storage.schema import migrate  # noqa: E402


def connect(database_url: str | None = None):
    import pymysql

    settings = StorageSettings.from_env()
    if database_url:
        settings = dataclasses.replace(settings, database_url=database_url)
    if not settings.enabled:
        raise SystemExit("Set DATABASE_URL or pass --database-url")
    return pymysql.connect(**settings.connect_kwargs())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--database-url", help="default: $DATABASE_URL")
    args = ap.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    conn = connect(args.database_url)
    try:
        migrate(conn)
        dataset_id, n = load_manifest(conn, manifest)
    finally:
        conn.close()
    print(f"Loaded {n} clips as dataset {manifest['dataset']!r} (id {dataset_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
