"""MySQL schema for VoiceGuard, applied as numbered migrations.

Tables (all prefixed vg_):
  vg_sessions_recent     the newest calls: at most DB_RECENT_ROWS (300), none older than DB_RECENT_DAYS (5)
  vg_sessions_archive    calls pushed out of recent; emptied into the trash every DB_ARCHIVE_RESET_DAYS (5)
  vg_sessions_trash      calls removed by age, capacity, archive reset or the user; purged after a while
  vg_verdicts            the call-level result of each call (one row per session_uid)
  vg_feedback            "was this result right?" answers from the user
  vg_archive_cycles      one row per archive cycle, so the 5-day reset survives restarts
  vg_schema_migrations   which of the migrations below have run

A call keeps the same session_uid in whichever vg_sessions_* table it is in, so its verdict and
feedback never move: only the session row does. No audio is stored, ever.

The SQL sticks to MySQL 8 syntax that MariaDB 10.6+ also accepts.
"""
from __future__ import annotations

import logging

log = logging.getLogger("voiceguard.storage")

# Columns every vg_sessions_* table shares, in the order INSERT ... SELECT copies them.
SESSION_COLUMNS = (
    "session_uid", "client_session_id", "client_hash", "source", "consent_given", "input_sample_rate",
    "file_name", "file_type", "file_size_bytes", "audio_seconds", "windows_analysed", "windows_dropped",
    "end_reason", "app_version", "started_at", "ended_at", "created_at",
)

_SESSION_DDL = """
    session_uid        CHAR(32)      NOT NULL,
    client_session_id  VARCHAR(64)   NOT NULL,
    client_hash        CHAR(64)      NULL     COMMENT 'sha256 of the browser''s anonymous id',
    source             ENUM('mic','tab','live','file') NOT NULL,
    consent_given      TINYINT(1)    NULL     COMMENT 'consent box as reported by the client',
    input_sample_rate  INT UNSIGNED  NULL,
    file_name          VARCHAR(255)  NULL,
    file_type          VARCHAR(100)  NULL,
    file_size_bytes    INT UNSIGNED  NULL,
    audio_seconds      DOUBLE        NOT NULL DEFAULT 0,
    windows_analysed   INT UNSIGNED  NOT NULL DEFAULT 0,
    windows_dropped    INT UNSIGNED  NOT NULL DEFAULT 0,
    end_reason         ENUM('stopped','completed','disconnected','idle_timeout','error') NOT NULL,
    app_version        VARCHAR(32)   NULL,
    started_at         DATETIME(3)   NULL,
    ended_at           DATETIME(3)   NOT NULL,
    created_at         DATETIME(3)   NOT NULL,
"""

_TABLE_OPTIONS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"

MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (1, "initial tables", [
        f"""CREATE TABLE IF NOT EXISTS vg_sessions_recent ({_SESSION_DDL}
            PRIMARY KEY (session_uid),
            KEY idx_recent_created (created_at),
            KEY idx_recent_client (client_hash, created_at)
        ) {_TABLE_OPTIONS} COMMENT='Newest calls (hot tier)'""",
        f"""CREATE TABLE IF NOT EXISTS vg_sessions_archive ({_SESSION_DDL}
            archived_at    DATETIME(3)  NOT NULL,
            archive_cycle  INT UNSIGNED NOT NULL,
            PRIMARY KEY (session_uid),
            KEY idx_archive_created (created_at),
            KEY idx_archive_client (client_hash, created_at)
        ) {_TABLE_OPTIONS} COMMENT='Older calls; emptied into the trash every archive cycle'""",
        f"""CREATE TABLE IF NOT EXISTS vg_sessions_trash ({_SESSION_DDL}
            trashed_at     DATETIME(3)  NOT NULL,
            trash_reason   ENUM('expired','capacity','archive_reset','user_deleted') NOT NULL,
            trashed_from   ENUM('recent','archive') NOT NULL,
            PRIMARY KEY (session_uid),
            KEY idx_trash_trashed (trashed_at)
        ) {_TABLE_OPTIONS} COMMENT='Removed calls, restorable until purged'""",
        f"""CREATE TABLE IF NOT EXISTS vg_verdicts (
            session_uid     CHAR(32)      NOT NULL,
            verdict         ENUM('safe','ai_voice','scam','inconclusive') NOT NULL,
            title           VARCHAR(120)  NOT NULL,
            message         VARCHAR(500)  NOT NULL,
            scam_score      FLOAT         NOT NULL,
            max_risk        ENUM('low','medium','high') NOT NULL,
            reasons         JSON          NOT NULL COMMENT 'scam tactics matched, strongest first',
            ai_voice        TINYINT(1)    NOT NULL,
            voice_score     FLOAT         NULL,
            voice_flagged   SMALLINT UNSIGNED NOT NULL DEFAULT 0,
            voice_windows   SMALLINT UNSIGNED NOT NULL DEFAULT 0,
            windows         INT UNSIGNED  NOT NULL DEFAULT 0,
            speech_seconds  FLOAT         NOT NULL DEFAULT 0,
            languages       VARCHAR(64)   NULL,
            window_summary  JSON          NULL COMMENT 'per window: time, risk, scores; no transcript',
            transcript      MEDIUMTEXT    NULL COMMENT 'only when DB_STORE_TRANSCRIPTS=true',
            created_at      DATETIME(3)   NOT NULL,
            PRIMARY KEY (session_uid),
            KEY idx_verdicts_kind (verdict, created_at)
        ) {_TABLE_OPTIONS} COMMENT='Call-level result, one per session'""",
        f"""CREATE TABLE IF NOT EXISTS vg_feedback (
            feedback_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            session_uid    CHAR(32)      NOT NULL,
            client_hash    CHAR(64)      NOT NULL,
            is_correct     TINYINT(1)    NOT NULL,
            actual_label   ENUM('safe','ai_voice','scam','unsure') NULL,
            comment        VARCHAR(500)  NULL,
            created_at     DATETIME(3)   NOT NULL,
            updated_at     DATETIME(3)   NOT NULL,
            PRIMARY KEY (feedback_id),
            UNIQUE KEY uq_feedback_session_client (session_uid, client_hash)
        ) {_TABLE_OPTIONS} COMMENT='User feedback on a verdict'""",
        f"""CREATE TABLE IF NOT EXISTS vg_archive_cycles (
            cycle_id      INT UNSIGNED  NOT NULL AUTO_INCREMENT,
            started_at    DATETIME(3)   NOT NULL,
            reset_at      DATETIME(3)   NULL,
            rows_trashed  INT UNSIGNED  NOT NULL DEFAULT 0,
            PRIMARY KEY (cycle_id)
        ) {_TABLE_OPTIONS} COMMENT='Archive reset history'""",
    ]),
]


def migrate(conn) -> int:
    """Applies any migrations not yet recorded. Safe to run from several processes at once."""
    with conn.cursor() as cur:
        cur.execute(
            f"""CREATE TABLE IF NOT EXISTS vg_schema_migrations (
                version     INT UNSIGNED NOT NULL,
                name        VARCHAR(100) NOT NULL,
                applied_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                PRIMARY KEY (version)
            ) {_TABLE_OPTIONS}"""
        )
        cur.execute("SELECT GET_LOCK('vg_migrate', 30)")
        try:
            cur.execute("SELECT version FROM vg_schema_migrations")
            done = {row[0] for row in cur.fetchall()}
            applied = 0
            for version, name, statements in MIGRATIONS:
                if version in done:
                    continue
                for sql in statements:                 # DDL commits implicitly in MySQL
                    cur.execute(sql)
                cur.execute("INSERT INTO vg_schema_migrations (version, name) VALUES (%s, %s)", (version, name))
                conn.commit()
                log.info("database migration %d applied: %s", version, name)
                applied += 1
            return applied
        finally:
            cur.execute("SELECT RELEASE_LOCK('vg_migrate')")
