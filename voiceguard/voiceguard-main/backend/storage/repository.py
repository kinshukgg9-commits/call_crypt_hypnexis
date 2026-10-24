"""SQL for VoiceGuard storage. Every function takes an open PyMySQL connection and commits its own work.

Record lifecycle (run_maintenance), in this order:
  1. archive reset   every DB_ARCHIVE_RESET_DAYS the whole archive moves to the trash
  2. expiry          any call older than DB_TRASH_AFTER_DAYS moves to the trash
  3. ageing          calls older than DB_RECENT_DAYS move from recent to archive
  4. recent cap      beyond DB_RECENT_ROWS, the oldest recent calls move to archive
  5. capacity        beyond DB_MAX_ROWS (recent + archive), the oldest calls move to the trash
  6. purge           trash older than DB_TRASH_RETENTION_DAYS, or beyond DB_MAX_TRASH_ROWS, is deleted
                     for good, together with its verdict and feedback
Moves are INSERT ... SELECT + DELETE by primary key in one transaction, so a call is never in two
tables at once and never lost half-way.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Sequence

from backend.storage.config import StorageSettings
from backend.storage.records import CallRecord, utc
from backend.storage.schema import SESSION_COLUMNS

_COLS = ", ".join(SESSION_COLUMNS)
_CHUNK = 500
TIERS = {"recent": "vg_sessions_recent", "archive": "vg_sessions_archive", "trash": "vg_sessions_trash"}

_INSERT_SESSION = f"INSERT INTO vg_sessions_recent ({_COLS}) VALUES ({', '.join(['%s'] * len(SESSION_COLUMNS))})"
_INSERT_VERDICT = (
    "INSERT INTO vg_verdicts (session_uid, verdict, title, message, scam_score, max_risk, reasons, ai_voice, "
    "voice_score, voice_flagged, voice_windows, windows, speech_seconds, languages, window_summary, transcript, "
    "created_at) VALUES (" + ", ".join(["%s"] * 17) + ")"
)


# -- writes -----------------------------------------------------------------------------
def insert_records(conn, records: Sequence[CallRecord], now: float) -> int:
    """Stores calls in vg_sessions_recent + vg_verdicts. PyMySQL turns executemany into one multi-row INSERT."""
    if not records:
        return 0
    with conn.cursor() as cur:
        cur.executemany(_INSERT_SESSION, [r.session_row(now) for r in records])
        cur.executemany(_INSERT_VERDICT, [r.verdict_row(now) for r in records])
    conn.commit()
    return len(records)


def _in(uids: Sequence[str]) -> str:
    return ", ".join(["%s"] * len(uids))


def _move(cur, src: str, dst: str, uids: Sequence[str], extra_cols: str = "", extra_vals: tuple = ()) -> int:
    """Moves rows by primary key from one vg_sessions_* table to another."""
    moved = 0
    for i in range(0, len(uids), _CHUNK):
        chunk = list(uids[i:i + _CHUNK])
        placeholders = ", ".join(["%s"] * len(extra_vals))
        cur.execute(
            f"INSERT INTO {TIERS[dst]} ({_COLS}{extra_cols}) "
            f"SELECT {_COLS}{', ' + placeholders if extra_vals else ''} FROM {TIERS[src]} "
            f"WHERE session_uid IN ({_in(chunk)})",
            (*extra_vals, *chunk),
        )
        cur.execute(f"DELETE FROM {TIERS[src]} WHERE session_uid IN ({_in(chunk)})", chunk)
        moved += cur.rowcount
    return moved


def _to_trash(cur, src: str, uids: Sequence[str], reason: str, now) -> int:
    return _move(cur, src, "trash", uids, ", trashed_at, trash_reason, trashed_from", (now, reason, src))


def _to_archive(cur, uids: Sequence[str], now, cycle_id: int) -> int:
    return _move(cur, "recent", "archive", uids, ", archived_at, archive_cycle", (now, cycle_id))


def _uids(cur, sql: str, args: tuple = ()) -> list[str]:
    cur.execute(sql, args)
    return [row[0] for row in cur.fetchall()]


def _count(cur, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return int(cur.fetchone()[0])


def _current_cycle(cur, now) -> tuple[int, Any]:
    cur.execute("SELECT cycle_id, started_at FROM vg_archive_cycles WHERE reset_at IS NULL "
                "ORDER BY cycle_id DESC LIMIT 1 FOR UPDATE")
    row = cur.fetchone()
    if row:
        return int(row[0]), row[1]
    cur.execute("INSERT INTO vg_archive_cycles (started_at) VALUES (%s)", (now,))
    return int(cur.lastrowid), now


def _purge(cur, uids: Sequence[str]) -> int:
    purged = 0
    for i in range(0, len(uids), _CHUNK):
        chunk = list(uids[i:i + _CHUNK])
        cur.execute(f"DELETE FROM vg_feedback WHERE session_uid IN ({_in(chunk)})", chunk)
        cur.execute(f"DELETE FROM vg_verdicts WHERE session_uid IN ({_in(chunk)})", chunk)
        cur.execute(f"DELETE FROM vg_sessions_trash WHERE session_uid IN ({_in(chunk)})", chunk)
        purged += cur.rowcount
    return purged


def run_maintenance(conn, s: StorageSettings, now_ts: float) -> dict[str, int] | None:
    """Applies the lifecycle above. Returns what moved, or None if another process holds the lock."""
    now = utc(now_ts)
    out = dict(archive_reset=0, expired=0, aged=0, recent_overflow=0, capacity=0, purged=0)
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('vg_maintenance', 0)")
        if cur.fetchone()[0] != 1:
            conn.rollback()
            return None
        try:
            # 1. archive reset
            cycle_id, started = _current_cycle(cur, now)
            if now - started >= timedelta(days=s.archive_reset_days):
                uids = _uids(cur, "SELECT session_uid FROM vg_sessions_archive FOR UPDATE")
                out["archive_reset"] = _to_trash(cur, "archive", uids, "archive_reset", now)
                cur.execute("UPDATE vg_archive_cycles SET reset_at = %s, rows_trashed = %s WHERE cycle_id = %s",
                            (now, out["archive_reset"], cycle_id))
                cur.execute("INSERT INTO vg_archive_cycles (started_at) VALUES (%s)", (now,))
                cycle_id = int(cur.lastrowid)

            # 2. expiry
            cutoff = now - timedelta(days=s.trash_after_days)
            for tier in ("recent", "archive"):
                uids = _uids(cur, f"SELECT session_uid FROM {TIERS[tier]} WHERE created_at < %s FOR UPDATE", (cutoff,))
                out["expired"] += _to_trash(cur, tier, uids, "expired", now)

            # 3. ageing, 4. recent cap
            cutoff = now - timedelta(days=s.recent_days)
            uids = _uids(cur, "SELECT session_uid FROM vg_sessions_recent WHERE created_at < %s FOR UPDATE", (cutoff,))
            out["aged"] = _to_archive(cur, uids, now, cycle_id)
            extra = _count(cur, "vg_sessions_recent") - s.recent_rows
            if extra > 0:
                uids = _uids(cur, "SELECT session_uid FROM vg_sessions_recent ORDER BY created_at, session_uid "
                                  "LIMIT %s FOR UPDATE", (extra,))
                out["recent_overflow"] = _to_archive(cur, uids, now, cycle_id)

            # 5. capacity: the oldest calls go first, from the archive before the recent table
            extra = _count(cur, "vg_sessions_recent") + _count(cur, "vg_sessions_archive") - s.max_live_rows
            for tier in ("archive", "recent"):
                if extra <= 0:
                    break
                uids = _uids(cur, f"SELECT session_uid FROM {TIERS[tier]} ORDER BY created_at, session_uid "
                                  "LIMIT %s FOR UPDATE", (extra,))
                moved = _to_trash(cur, tier, uids, "capacity", now)
                out["capacity"] += moved
                extra -= moved

            # 6. purge
            cutoff = now - timedelta(days=s.trash_retention_days)
            uids = _uids(cur, "SELECT session_uid FROM vg_sessions_trash WHERE trashed_at < %s", (cutoff,))
            out["purged"] = _purge(cur, uids)
            extra = _count(cur, "vg_sessions_trash") - s.max_trash_rows
            if extra > 0:
                uids = _uids(cur, "SELECT session_uid FROM vg_sessions_trash ORDER BY trashed_at, created_at "
                                  "LIMIT %s", (extra,))
                out["purged"] += _purge(cur, uids)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.execute("SELECT RELEASE_LOCK('vg_maintenance')")
    return out


# -- user actions ---------------------------------------------------------------------------
def find_tier(cur, session_uid: str, lock: bool = False) -> tuple[str, str | None] | None:
    """(tier, client_hash) of a call, or None."""
    for tier, table in TIERS.items():
        cur.execute(f"SELECT client_hash FROM {table} WHERE session_uid = %s{' FOR UPDATE' if lock else ''}",
                    (session_uid,))
        row = cur.fetchone()
        if row:
            return tier, row[0]
    return None


def trash_call(conn, session_uid: str, client_hash: str | None, now_ts: float) -> str:
    """Moves one call to the trash for its owner. Returns 'ok', 'not_found' or 'forbidden'."""
    with conn.cursor() as cur:
        found = find_tier(cur, session_uid, lock=True)
        if not found or found[0] == "trash":
            conn.rollback()
            return "not_found"
        if client_hash is None or found[1] != client_hash:
            conn.rollback()
            return "forbidden"
        _to_trash(cur, found[0], [session_uid], "user_deleted", utc(now_ts))
    conn.commit()
    return "ok"


def restore_call(conn, session_uid: str, s: StorageSettings, now_ts: float) -> str:
    """Moves a call from the trash back to recent. Returns 'ok', 'not_found' or 'expired'."""
    with conn.cursor() as cur:
        cur.execute("SELECT created_at FROM vg_sessions_trash WHERE session_uid = %s FOR UPDATE", (session_uid,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return "not_found"
        if utc(now_ts) - row[0] >= timedelta(days=s.trash_after_days):
            conn.rollback()
            return "expired"                       # it would go straight back to the trash
        _move(cur, "trash", "recent", [session_uid])
    conn.commit()
    return "ok"


def save_feedback(conn, session_uid: str, client_hash: str, is_correct: bool, actual_label: str | None,
                  comment: str | None, now_ts: float) -> str | None:
    """Creates or replaces this browser's feedback on a call. Returns 'created', 'updated' or None (no such call)."""
    now = utc(now_ts)
    with conn.cursor() as cur:
        found = find_tier(cur, session_uid)
        if not found or found[0] == "trash":
            conn.rollback()
            return None
        cur.execute("SELECT feedback_id FROM vg_feedback WHERE session_uid = %s AND client_hash = %s FOR UPDATE",
                    (session_uid, client_hash))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE vg_feedback SET is_correct = %s, actual_label = %s, comment = %s, updated_at = %s "
                        "WHERE feedback_id = %s", (int(is_correct), actual_label, comment, now, row[0]))
        else:
            cur.execute("INSERT INTO vg_feedback (session_uid, client_hash, is_correct, actual_label, comment, "
                        "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (session_uid, client_hash, int(is_correct), actual_label, comment, now, now))
    conn.commit()
    return "updated" if row else "created"


# -- reads ----------------------------------------------------------------------------------
_HISTORY_FIELDS = ("session_uid, source, file_name, audio_seconds, started_at, ended_at, created_at, "
                   "end_reason")


def _iso(value) -> str | None:
    return None if value is None else value.isoformat(timespec="milliseconds") + "Z"


def list_history(conn, client_hash: str, limit: int, offset: int = 0) -> list[dict[str, Any]]:
    """This browser's calls in recent + archive, newest first, with their verdicts."""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT h.*, v.verdict, v.title, v.scam_score, v.ai_voice, v.voice_score, v.reasons,
                       EXISTS (SELECT 1 FROM vg_feedback f WHERE f.session_uid = h.session_uid
                               AND f.client_hash = %s) AS has_feedback
                FROM (SELECT {_HISTORY_FIELDS}, 'recent' AS tier FROM vg_sessions_recent WHERE client_hash = %s
                      UNION ALL
                      SELECT {_HISTORY_FIELDS}, 'archive' AS tier FROM vg_sessions_archive WHERE client_hash = %s) h
                LEFT JOIN vg_verdicts v ON v.session_uid = h.session_uid
                ORDER BY h.created_at DESC, h.ended_at DESC LIMIT %s OFFSET %s""",
            (client_hash, client_hash, client_hash, limit, offset),
        )
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, row)) for row in cur.fetchall()]
    conn.commit()
    for r in rows:
        for key in ("started_at", "ended_at", "created_at"):
            r[key] = _iso(r[key])
        r["reasons"] = json.loads(r["reasons"]) if r["reasons"] else []
        r["ai_voice"] = None if r["ai_voice"] is None else bool(r["ai_voice"])
        r["has_feedback"] = bool(r["has_feedback"])
    return rows


def get_call(conn, session_uid: str) -> dict[str, Any] | None:
    """One call from any tier, with its verdict and feedback."""
    with conn.cursor() as cur:
        found = find_tier(cur, session_uid)
        if not found:
            conn.commit()
            return None
        tier = found[0]
        cur.execute(f"SELECT * FROM {TIERS[tier]} WHERE session_uid = %s", (session_uid,))
        names = [d[0] for d in cur.description]
        call = dict(zip(names, cur.fetchone()))
        cur.execute("SELECT * FROM vg_verdicts WHERE session_uid = %s", (session_uid,))
        row = cur.fetchone()
        verdict = dict(zip([d[0] for d in cur.description], row)) if row else None
        cur.execute("SELECT client_hash, is_correct, actual_label, comment, created_at, updated_at "
                    "FROM vg_feedback WHERE session_uid = %s ORDER BY feedback_id", (session_uid,))
        feedback = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    conn.commit()
    call["tier"] = tier
    call["verdict"] = verdict
    call["feedback"] = feedback
    return call


def stats(conn, s: StorageSettings) -> dict[str, Any]:
    with conn.cursor() as cur:
        counts = {tier: _count(cur, table) for tier, table in TIERS.items()}
        cur.execute("SELECT COUNT(*) FROM vg_feedback")
        feedback = int(cur.fetchone()[0])
        cur.execute("SELECT cycle_id, started_at FROM vg_archive_cycles WHERE reset_at IS NULL "
                    "ORDER BY cycle_id DESC LIMIT 1")
        cycle = cur.fetchone()
        cur.execute("SELECT MIN(created_at) FROM vg_sessions_recent")
        oldest_recent = cur.fetchone()[0]
    conn.commit()
    return {
        "counts": {**counts, "feedback": feedback, "live": counts["recent"] + counts["archive"]},
        "limits": {
            "recent_rows": s.recent_rows, "recent_days": s.recent_days, "max_live_rows": s.max_live_rows,
            "archive_reset_days": s.archive_reset_days, "trash_after_days": s.trash_after_days,
            "trash_retention_days": s.trash_retention_days, "max_trash_rows": s.max_trash_rows,
        },
        "archive_cycle": None if not cycle else {
            "cycle_id": int(cycle[0]),
            "started_at": _iso(cycle[1]),
            "next_reset_at": _iso(cycle[1] + timedelta(days=s.archive_reset_days)),
        },
        "oldest_recent_at": _iso(oldest_recent),
    }
