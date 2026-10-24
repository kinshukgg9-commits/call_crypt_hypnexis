# VGSC-25: VoiceGuard Scam Calls 2025 (test set)

A held-out test set for the VoiceGuard backend, built from scam calls reported in 2025.
Read `DATASHEET.md` for what is in it and how it was checked.

    benchmark/vgsc25/
      incidents.jsonl     25 verified 2025 cases, campaigns and statistics (levels A/B/C)
      sources.csv         40 sources: URL, publisher, type, date read, fields supported
      excluded.json       10 rejected candidates and why
      calls.jsonl         57 call scripts: 31 scam (each tied to an incident), 26 genuine look-alikes
      audio/              171 FLAC clips: each call clean, over G.711, and over GSM + 10 dB noise
      partial_fakes/      the earlier 130-clip partial-fake set (100 fake, 30 genuine)
      results/            baseline results of today's backend
      incidents_src.py, calls_src.py    the sources the JSONL files are generated from

## Run it

    python scripts/eval_scam_benchmark.py text     # scam rules + verdict on the transcripts (seconds)
    python scripts/eval_scam_benchmark.py voice    # voice model on the audio (a few minutes)
    python scripts/eval_scam_benchmark.py full     # whole pipeline, as an upload (needs faster-whisper)
    python scripts/check_benchmark_overlap.py --train-cache data/voice/cache   # held-out check

## Load it into MySQL (only when you decide to)

    python scripts/load_scam_benchmark.py --dry-run
    DATABASE_URL=mysql://user:pass@host:3306/voiceguard python scripts/load_scam_benchmark.py

Creates four tables (migration 3: `vg_eval_incidents`, `vg_eval_incident_sources`,
`vg_eval_calls`, `vg_eval_call_clips`) and loads the partial fakes as `partial-fakes-v2`.
It checks every file's SHA-256 first. Loading twice gives the same rows. Audio stays in files;
the tables hold the path and hash.

## Baseline (today's backend, 2026-10-24)

| Check | Result |
|---|---|
| Scam text rules, 57 calls | precision 0.91, recall 0.61, false alarms 2 of 26, median 17 s to verdict |
| Voice model, clean / G.711 / GSM + noise | 57/57, 57/57, 22/57 synthetic calls flagged |

Cite with `CITATION.cff`.
