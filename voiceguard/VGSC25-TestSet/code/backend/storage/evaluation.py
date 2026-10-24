"""SQL for the evaluation tables (vg_eval_*): labelled test sets such as the 100 partial fakes,
and the results of localisation runs on them. These rows are not calls: the call lifecycle
(cap, trash, archive reset) never touches them.
"""
from __future__ import annotations

import json
import time
from typing import Any

from backend.storage.records import utc


def load_manifest(conn, manifest: dict[str, Any], now_ts: float | None = None) -> tuple[int, int]:
    """Creates or replaces a dataset from a scripts/build_partial_fakes.py manifest.
    Returns (dataset_id, clips loaded). Loading the same manifest twice gives the same rows."""
    now = utc(now_ts if now_ts is not None else time.time())
    with conn.cursor() as cur:
        cur.execute("SELECT dataset_id FROM vg_eval_datasets WHERE name = %s FOR UPDATE", (manifest["dataset"],))
        row = cur.fetchone()
        if row:
            dataset_id = int(row[0])
            cur.execute("DELETE FROM vg_eval_clips WHERE dataset_id = %s", (dataset_id,))   # cascades
            cur.execute("UPDATE vg_eval_datasets SET description = %s, frame_seconds = %s, sample_rate = %s, "
                        "fake_source = %s WHERE dataset_id = %s",
                        (manifest.get("description") or None, manifest["frame_seconds"], manifest["sample_rate"],
                         manifest.get("fake_source"), dataset_id))
        else:
            cur.execute("INSERT INTO vg_eval_datasets (name, description, frame_seconds, sample_rate, fake_source, "
                        "created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                        (manifest["dataset"], manifest.get("description") or None, manifest["frame_seconds"],
                         manifest["sample_rate"], manifest.get("fake_source"), now))
            dataset_id = int(cur.lastrowid)
        for clip in manifest["clips"]:
            fakes = [s for s in clip["segments"] if s["label"] == "fake"]
            cur.execute(
                "INSERT INTO vg_eval_clips (dataset_id, clip_name, kind, file_path, sha256, duration_s, sample_rate, "
                "real_speaker, fake_engine, fake_seconds, fake_share, fake_segments, frame_labels, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (dataset_id, clip["name"], "partial" if fakes else "genuine", clip["file"], clip["sha256"],
                 clip["duration"], clip["sample_rate"], clip.get("real_speaker"), clip.get("fake_engine"),
                 clip["fake_seconds"], clip["fake_share"], len(fakes), clip["frame_labels"], now),
            )
            clip_id = int(cur.lastrowid)
            cur.executemany(
                "INSERT INTO vg_eval_segments (clip_id, seg_index, start_s, end_s, label, source, spoken_text) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [(clip_id, i, s["start"], s["end"], s["label"], (s.get("source") or "")[:150] or None,
                  (s.get("text") or "")[:255] or None) for i, s in enumerate(clip["segments"])],
            )
    conn.commit()
    return dataset_id, len(manifest["clips"])


def dataset_clips(conn, name: str) -> tuple[int, float, list[dict[str, Any]]]:
    """(dataset_id, frame_seconds, clips) where each clip has clip_id, clip_name, file_path, frame_labels."""
    with conn.cursor() as cur:
        cur.execute("SELECT dataset_id, frame_seconds FROM vg_eval_datasets WHERE name = %s", (name,))
        row = cur.fetchone()
        if not row:
            conn.commit()
            raise LookupError(f"No dataset named {name!r}; load it with scripts/load_partial_fakes.py")
        cur.execute("SELECT clip_id, clip_name, file_path, sha256, frame_labels FROM vg_eval_clips "
                    "WHERE dataset_id = %s ORDER BY clip_name", (row[0],))
        names = [d[0] for d in cur.description]
        clips = [dict(zip(names, r)) for r in cur.fetchall()]
    conn.commit()
    return int(row[0]), float(row[1]), clips


def save_run(conn, dataset_id: int, run: dict[str, Any], predictions: list[dict[str, Any]], now_ts: float | None = None) -> int:
    """Stores one evaluation run and its per-clip predictions. Returns run_id."""
    now = utc(now_ts if now_ts is not None else time.time())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO vg_eval_runs (dataset_id, model_name, window_seconds, threshold, frame_precision, frame_recall, "
            "frame_f1, best_threshold, best_f1, clips_scored, notes, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (dataset_id, run["model_name"][:150], run["window_seconds"], run["threshold"], run["precision"],
             run["recall"], run["f1"], run.get("best_threshold"), run.get("best_f1"), len(predictions),
             (run.get("notes") or "")[:500] or None, now),
        )
        run_id = int(cur.lastrowid)
        cur.executemany(
            "INSERT INTO vg_eval_predictions (run_id, clip_id, frame_scores, frame_pred, tp, fp, fn, tn, f1) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(run_id, p["clip_id"], json.dumps([None if s is None else round(s, 4) for s in p["scores"]]),
              p["pred"], p["tp"], p["fp"], p["fn"], p["tn"], round(p["f1"], 4)) for p in predictions],
        )
    conn.commit()
    return run_id


def load_scam_benchmark(conn, incidents: list[dict], calls: list[dict], audio: dict | None,
                        dataset: str = "vgsc25-calls", now_ts: float | None = None) -> dict[str, int]:
    """Creates or replaces the VGSC-25 scam-call test set (benchmark/vgsc25). Returns row counts.
    Loading it twice gives the same rows."""
    now = utc(now_ts if now_ts is not None else time.time())
    j = lambda v: json.dumps(v, ensure_ascii=False)
    with conn.cursor() as cur:
        cur.execute("SELECT dataset_id FROM vg_eval_datasets WHERE name = %s FOR UPDATE", (dataset,))
        row = cur.fetchone()
        if row:
            dataset_id = int(row[0])
            cur.execute("DELETE FROM vg_eval_calls WHERE dataset_id = %s", (dataset_id,))       # cascades to clips
        else:
            cur.execute("INSERT INTO vg_eval_datasets (name, description, frame_seconds, sample_rate, fake_source, "
                        "created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                        (dataset, "VGSC-25: reconstructed 2025 scam calls and look-alike genuine calls",
                         0.5, 16000, "offline stock TTS voices", now))
            dataset_id = int(cur.lastrowid)
        uids = [i["id"] for i in incidents]
        if uids:
            cur.execute(f"DELETE FROM vg_eval_incidents WHERE incident_uid IN ({', '.join(['%s'] * len(uids))})", uids)
        ids = {}
        for i in incidents:
            cur.execute(
                "INSERT INTO vg_eval_incidents (incident_uid, record_type, event_date, date_precision, country, region, "
                "scam_type, claimed_identity, channel, ai_voice, victim_profile, loss_amount, currency, loss_usd_approx, "
                "recovered_amount, outcome, tactics, summary, verification_level, note, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (i["id"], i["record_type"], i["date"], i["date_precision"], i["country"], i.get("region"),
                 i["scam_type"], j(i["claimed_identity"]), j(i["channel"]), i["ai_voice"], i.get("victim_profile"),
                 i.get("loss"), i.get("currency"), i.get("loss_usd_approx"), i.get("recovered"),
                 (i.get("outcome") or "")[:500] or None, j(i["tactics"]), i["summary"], i["verification_level"],
                 (i.get("note") or "")[:500] or None, now))
            ids[i["id"]] = int(cur.lastrowid)
            cur.executemany(
                "INSERT INTO vg_eval_incident_sources (incident_id, source_index, url, publisher, source_type, published, "
                "accessed, supports, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(ids[i["id"]], k, s["url"][:500], s["publisher"][:150], s["source_type"], s.get("published"),
                  s.get("accessed"), j(s["supports"]), (s.get("note") or "")[:300] or None)
                 for k, s in enumerate(i["sources"])])
        call_ids = {}
        for c in calls:
            cur.execute(
                "INSERT INTO vg_eval_calls (dataset_id, call_uid, incident_id, label, scam_type, language, turns, note, "
                "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (dataset_id, c["id"], ids.get(c.get("incident_id")), c["label"], c["scam_type"], c["language"],
                 j(c["turns"]), (c.get("note") or "")[:300] or None, now))
            call_ids[c["id"]] = int(cur.lastrowid)
        clips = (audio or {}).get("clips", [])
        cur.executemany(
            "INSERT INTO vg_eval_call_clips (call_id, clip_name, line_condition, file_path, sha256, duration_s, "
            "voice_label, voices, turn_times, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(call_ids[k["call_id"]], k["id"], k["condition"], k["file"], k["sha256"], k["duration"], k["voice_label"],
              j(sorted({t["voice"] for t in k["turns"]})),
              j([{"speaker": t["speaker"], "start": t["start"], "end": t["end"]} for t in k["turns"]]), now)
             for k in clips])
    conn.commit()
    return {"incidents": len(incidents), "sources": sum(len(i["sources"]) for i in incidents),
            "calls": len(calls), "clips": len(clips), "dataset_id": dataset_id}
