-- VoiceGuard MySQL schema (MySQL 8.0+ or MariaDB 10.6+).
-- The backend creates these tables by itself on first start (backend/storage/schema.py);
-- this file is for creating them by hand, e.g. in MySQL Workbench, or for review.
-- Generated from backend/storage/schema.py: edit that file, not this one.

CREATE DATABASE IF NOT EXISTS voiceguard CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE voiceguard;

CREATE TABLE IF NOT EXISTS vg_schema_migrations (
    version     INT UNSIGNED NOT NULL,
    name        VARCHAR(100) NOT NULL,
    applied_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Migration 1: initial tables
CREATE TABLE IF NOT EXISTS vg_sessions_recent (
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
    PRIMARY KEY (session_uid),
    KEY idx_recent_created (created_at),
    KEY idx_recent_client (client_hash, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Newest calls (hot tier)';

CREATE TABLE IF NOT EXISTS vg_sessions_archive (
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
    archived_at    DATETIME(3)  NOT NULL,
    archive_cycle  INT UNSIGNED NOT NULL,
    PRIMARY KEY (session_uid),
    KEY idx_archive_created (created_at),
    KEY idx_archive_client (client_hash, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Older calls; emptied into the trash every archive cycle';

CREATE TABLE IF NOT EXISTS vg_sessions_trash (
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
    trashed_at     DATETIME(3)  NOT NULL,
    trash_reason   ENUM('expired','capacity','archive_reset','user_deleted') NOT NULL,
    trashed_from   ENUM('recent','archive') NOT NULL,
    PRIMARY KEY (session_uid),
    KEY idx_trash_trashed (trashed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Removed calls, restorable until purged';

CREATE TABLE IF NOT EXISTS vg_verdicts (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Call-level result, one per session';

CREATE TABLE IF NOT EXISTS vg_feedback (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='User feedback on a verdict';

CREATE TABLE IF NOT EXISTS vg_archive_cycles (
    cycle_id      INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    started_at    DATETIME(3)   NOT NULL,
    reset_at      DATETIME(3)   NULL,
    rows_trashed  INT UNSIGNED  NOT NULL DEFAULT 0,
    PRIMARY KEY (cycle_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Archive reset history';

INSERT IGNORE INTO vg_schema_migrations (version, name) VALUES (1, 'initial tables');
