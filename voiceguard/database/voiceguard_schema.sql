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

-- Migration 2: evaluation datasets (partial fakes)
CREATE TABLE IF NOT EXISTS vg_eval_datasets (
    dataset_id     INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    name           VARCHAR(100)  NOT NULL,
    description    VARCHAR(500)  NULL,
    frame_seconds  DECIMAL(4,2)  NOT NULL DEFAULT 0.50,
    sample_rate    INT UNSIGNED  NOT NULL DEFAULT 16000,
    fake_source    VARCHAR(100)  NULL,
    created_at     DATETIME(3)   NOT NULL,
    PRIMARY KEY (dataset_id),
    UNIQUE KEY uq_eval_dataset_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Labelled test sets; never touched by the call lifecycle';

CREATE TABLE IF NOT EXISTS vg_eval_clips (
    clip_id        INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    dataset_id     INT UNSIGNED  NOT NULL,
    clip_name      VARCHAR(100)  NOT NULL,
    kind           ENUM('genuine','spoof','partial') NOT NULL DEFAULT 'partial',
    file_path      VARCHAR(255)  NOT NULL COMMENT 'relative to the dataset folder; audio is not in the database',
    sha256         CHAR(64)      NOT NULL,
    duration_s     DECIMAL(7,3)  NOT NULL,
    sample_rate    INT UNSIGNED  NOT NULL,
    real_speaker   VARCHAR(100)  NULL,
    fake_engine    VARCHAR(100)  NULL,
    fake_seconds   DECIMAL(7,3)  NOT NULL DEFAULT 0,
    fake_share     DECIMAL(5,3)  NOT NULL DEFAULT 0,
    fake_segments  TINYINT UNSIGNED NOT NULL DEFAULT 0,
    frame_labels   VARCHAR(255)  NOT NULL COMMENT 'one character per frame: 1 = synthetic',
    created_at     DATETIME(3)   NOT NULL,
    PRIMARY KEY (clip_id),
    UNIQUE KEY uq_eval_clip (dataset_id, clip_name),
    CONSTRAINT fk_eval_clip_dataset FOREIGN KEY (dataset_id) REFERENCES vg_eval_datasets (dataset_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='One clip of an evaluation set';

CREATE TABLE IF NOT EXISTS vg_eval_segments (
    clip_id        INT UNSIGNED  NOT NULL,
    seg_index      TINYINT UNSIGNED NOT NULL,
    start_s        DECIMAL(7,3)  NOT NULL,
    end_s          DECIMAL(7,3)  NOT NULL,
    label          ENUM('real','fake') NOT NULL,
    source         VARCHAR(150)  NULL COMMENT 'real speaker, or TTS engine and voice',
    spoken_text    VARCHAR(255)  NULL,
    PRIMARY KEY (clip_id, seg_index),
    CONSTRAINT fk_eval_segment_clip FOREIGN KEY (clip_id) REFERENCES vg_eval_clips (clip_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Ground truth: which stretches of a clip are synthetic';

CREATE TABLE IF NOT EXISTS vg_eval_runs (
    run_id           INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    dataset_id       INT UNSIGNED  NOT NULL,
    model_name       VARCHAR(150)  NOT NULL,
    window_seconds   DECIMAL(5,2)  NOT NULL,
    threshold        DECIMAL(6,4)  NOT NULL,
    frame_precision  DECIMAL(6,4)  NOT NULL,
    frame_recall     DECIMAL(6,4)  NOT NULL,
    frame_f1         DECIMAL(6,4)  NOT NULL,
    best_threshold   DECIMAL(6,4)  NULL COMMENT 'tuned on this set: optimistic',
    best_f1          DECIMAL(6,4)  NULL,
    clips_scored     INT UNSIGNED  NOT NULL,
    notes            VARCHAR(500)  NULL,
    created_at       DATETIME(3)   NOT NULL,
    PRIMARY KEY (run_id),
    KEY idx_eval_runs_dataset (dataset_id, created_at),
    CONSTRAINT fk_eval_run_dataset FOREIGN KEY (dataset_id) REFERENCES vg_eval_datasets (dataset_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='One localisation evaluation of a model on a dataset';

CREATE TABLE IF NOT EXISTS vg_eval_predictions (
    run_id         INT UNSIGNED  NOT NULL,
    clip_id        INT UNSIGNED  NOT NULL,
    frame_scores   JSON          NOT NULL,
    frame_pred     VARCHAR(255)  NOT NULL,
    tp             SMALLINT UNSIGNED NOT NULL,
    fp             SMALLINT UNSIGNED NOT NULL,
    fn             SMALLINT UNSIGNED NOT NULL,
    tn             SMALLINT UNSIGNED NOT NULL,
    f1             DECIMAL(6,4)  NOT NULL,
    PRIMARY KEY (run_id, clip_id),
    CONSTRAINT fk_eval_pred_run FOREIGN KEY (run_id) REFERENCES vg_eval_runs (run_id) ON DELETE CASCADE,
    CONSTRAINT fk_eval_pred_clip FOREIGN KEY (clip_id) REFERENCES vg_eval_clips (clip_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Per-clip frame scores and results of a run';

-- Migration 3: scam-call test set (incidents, call scripts, audio renderings)
CREATE TABLE IF NOT EXISTS vg_eval_incidents (
    incident_id        INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    incident_uid       VARCHAR(20)   NOT NULL COMMENT 'e.g. IN-2025-005',
    record_type        ENUM('incident','campaign','enforcement','policy','statistic') NOT NULL,
    event_date         VARCHAR(10)   NOT NULL COMMENT 'YYYY, YYYY-MM or YYYY-MM-DD',
    date_precision     VARCHAR(10)   NOT NULL,
    country            CHAR(2)       NOT NULL,
    region             VARCHAR(100)  NULL,
    scam_type          VARCHAR(50)   NOT NULL,
    claimed_identity   JSON          NOT NULL,
    channel            JSON          NOT NULL,
    ai_voice           VARCHAR(30)   NOT NULL,
    victim_profile     VARCHAR(150)  NULL COMMENT 'never a private name',
    loss_amount        DECIMAL(16,2) NULL,
    currency           CHAR(3)       NULL,
    loss_usd_approx    DECIMAL(16,2) NULL,
    recovered_amount   DECIMAL(16,2) NULL,
    outcome            VARCHAR(500)  NULL,
    tactics            JSON          NOT NULL,
    summary            TEXT          NOT NULL,
    verification_level ENUM('A','B','C') NOT NULL COMMENT 'A official source, B two independent sources, C one source',
    note               VARCHAR(500)  NULL,
    created_at         DATETIME(3)   NOT NULL,
    PRIMARY KEY (incident_id),
    UNIQUE KEY uq_eval_incident_uid (incident_uid),
    KEY idx_eval_incident_type (scam_type, country)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Documented real-world scam calls behind the test set';

CREATE TABLE IF NOT EXISTS vg_eval_incident_sources (
    incident_id    INT UNSIGNED  NOT NULL,
    source_index   TINYINT UNSIGNED NOT NULL,
    url            VARCHAR(500)  NOT NULL,
    publisher      VARCHAR(150)  NOT NULL,
    source_type    ENUM('official','official_media','news','wire','database') NOT NULL,
    published      VARCHAR(10)   NULL,
    accessed       DATE          NULL COMMENT 'null: listed by another source, not read',
    supports       JSON          NOT NULL COMMENT 'the incident fields this source backs',
    note           VARCHAR(300)  NULL,
    PRIMARY KEY (incident_id, source_index),
    CONSTRAINT fk_eval_source_incident FOREIGN KEY (incident_id) REFERENCES vg_eval_incidents (incident_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Sources of each incident';

CREATE TABLE IF NOT EXISTS vg_eval_calls (
    call_id        INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    dataset_id     INT UNSIGNED  NOT NULL,
    call_uid       VARCHAR(20)   NOT NULL,
    incident_id    INT UNSIGNED  NULL COMMENT 'null for genuine calls',
    label          TINYINT UNSIGNED NOT NULL COMMENT '1 scam, 0 genuine',
    scam_type      VARCHAR(50)   NOT NULL,
    language       VARCHAR(10)   NOT NULL COMMENT 'en, hi (Devanagari), hi-Latn (romanized Hinglish)',
    turns          JSON          NOT NULL COMMENT '[{speaker, text, basis}]',
    note           VARCHAR(300)  NULL,
    created_at     DATETIME(3)   NOT NULL,
    PRIMARY KEY (call_id),
    UNIQUE KEY uq_eval_call (dataset_id, call_uid),
    CONSTRAINT fk_eval_call_dataset FOREIGN KEY (dataset_id) REFERENCES vg_eval_datasets (dataset_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_eval_call_incident FOREIGN KEY (incident_id) REFERENCES vg_eval_incidents (incident_id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Reconstructed call scripts with labels';

CREATE TABLE IF NOT EXISTS vg_eval_call_clips (
    call_clip_id   INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    call_id        INT UNSIGNED  NOT NULL,
    clip_name      VARCHAR(100)  NOT NULL,
    line_condition VARCHAR(30)   NOT NULL COMMENT 'clean, g711_mulaw, phone_worst (ml/channel.py)',
    file_path      VARCHAR(255)  NOT NULL COMMENT 'relative to benchmark/vgsc25/audio; audio is not in the database',
    sha256         CHAR(64)      NOT NULL,
    duration_s     DECIMAL(7,2)  NOT NULL,
    voice_label    ENUM('synthetic','real') NOT NULL,
    voices         JSON          NOT NULL,
    turn_times     JSON          NOT NULL COMMENT '[{speaker, start, end}]',
    created_at     DATETIME(3)   NOT NULL,
    PRIMARY KEY (call_clip_id),
    UNIQUE KEY uq_eval_call_clip (clip_name),
    CONSTRAINT fk_eval_call_clip_call FOREIGN KEY (call_id) REFERENCES vg_eval_calls (call_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Audio renderings of the call scripts';

INSERT IGNORE INTO vg_schema_migrations (version, name) VALUES (1, 'initial tables');
INSERT IGNORE INTO vg_schema_migrations (version, name) VALUES (2, 'evaluation datasets (partial fakes)');
INSERT IGNORE INTO vg_schema_migrations (version, name) VALUES (3, 'scam-call test set (incidents, call scripts, audio renderings)');
