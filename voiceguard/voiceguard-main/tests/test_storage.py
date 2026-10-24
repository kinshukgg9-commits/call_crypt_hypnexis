"""MySQL storage: tables, the record lifecycle, the REST endpoints and the non-blocking writer.

Database tests are opt-in: point VOICEGUARD_TEST_DATABASE_URL at an empty, throw-away database
(every vg_* table in it is dropped), e.g. mysql://vg:vgpass@127.0.0.1:3306/voiceguard_test
"""
from __future__ import annotations

import json
import os
import time

import pytest

from backend.schemas import Verdict
from backend.storage import CallRecord, Storage, StorageSettings, repository
from backend.storage.records import hash_client_id, mask_digits
from backend.storage.schema import MIGRATIONS, migrate
from tests.helpers import send_audio, tone

DB_URL = os.getenv("VOICEGUARD_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="set VOICEGUARD_TEST_DATABASE_URL to run MySQL tests")
DAY = 86400.0
T0 = 1_800_000_000.0           # a fixed "now" for the fake clock
CLIENT = "browser-test-1234"
ADMIN = "admin-secret"


class Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def verdict(kind: str = "safe") -> Verdict:
    return Verdict(verdict=kind, title="Not a scam call", message="Nothing matched.", final=True, scam_score=0.1,
                   reasons=["Urgency"], ai_voice=False, voice_score=0.0, voice_windows=1, windows=1,
                   speech_seconds=3.0)


def record(client: str | None = CLIENT, **kw) -> CallRecord:
    kw.setdefault("ended_at", T0)
    return CallRecord(client_session_id="s", source="mic", end_reason="stopped", verdict=verdict(),
                      client_hash=hash_client_id(client), **kw)


# -- no database needed ----------------------------------------------------------------
def test_storage_is_off_without_database_url():
    s = Storage(StorageSettings())
    assert not s.settings.enabled and s.status()["state"] == "disabled"
    assert s.submit(record()) is False


def test_database_url_parsing():
    kw = StorageSettings(database_url="mysql://u%40x:p%3Aw@db.example.com:3307/vg?ssl=true").connect_kwargs()
    assert (kw["host"], kw["port"], kw["user"], kw["password"], kw["database"]) == (
        "db.example.com", 3307, "u@x", "p:w", "vg")
    assert kw["ssl_verify_cert"] and kw["charset"] == "utf8mb4"
    with pytest.raises(ValueError):
        StorageSettings(database_url="postgres://x/y").connect_kwargs()
    with pytest.raises(ValueError):
        StorageSettings(database_url="mysql://u:p@host").connect_kwargs()


def test_client_ids_are_hashed_and_validated():
    assert len(hash_client_id(CLIENT)) == 64 and hash_client_id(CLIENT) != CLIENT
    assert hash_client_id("short") is None and hash_client_id("bad id with spaces") is None


def test_transcripts_mask_numbers():
    assert mask_digits("my OTP is 4 8 2 9 1 6, card 4111-1111") == "my OTP is [number], card [number]"


def test_storage_endpoints_absent_without_database(make_client):
    client, _, _ = make_client()
    assert client.get("/api/history", headers={"X-VoiceGuard-Client": CLIENT}).status_code == 404


def test_unreachable_database_never_blocks_analysis(make_client):
    storage = Storage(StorageSettings(database_url="mysql://u:p@127.0.0.1:1/none", connect_timeout=0.5))
    client, _, _ = make_client(texts=["hello"], storage=storage)
    with client.websocket_connect("/ws/down?client=" + CLIENT) as ws:
        ws.receive_json()
        send_audio(ws, tone(2.0))
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json()["type"] == "result"
        assert ws.receive_json()["type"] == "stopped"
    assert storage.status()["queued"] == 1                      # kept in memory for a retry
    r = client.get("/api/history", headers={"X-VoiceGuard-Client": CLIENT})
    assert r.status_code == 503 and r.json()["code"] == "storage_unavailable"


# -- MySQL ---------------------------------------------------------------------------------
def _conn():
    import pymysql

    return pymysql.connect(**StorageSettings(database_url=DB_URL).connect_kwargs())


@pytest.fixture
def db():
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'vg\\_%%'")
        for (table,) in cur.fetchall():
            cur.execute(f"DROP TABLE `{table}`")
    migrate(conn)
    yield conn
    conn.close()


def counts(conn) -> dict[str, int]:
    out = {}
    with conn.cursor() as cur:
        for tier, table in repository.TIERS.items():
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            out[tier] = cur.fetchone()[0]
    conn.commit()
    return out


def insert(conn, n: int, at: float, client: str | None = CLIENT) -> list[str]:
    records = [record(client) for _ in range(n)]
    repository.insert_records(conn, records, at)
    return [r.session_uid for r in records]


@needs_db
def test_migrations_create_every_table_once(db):
    assert migrate(db) == 0                                       # already applied by the fixture
    with db.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'vg\\_%%'")
        tables = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT MAX(version) FROM vg_schema_migrations")
        assert cur.fetchone()[0] == MIGRATIONS[-1][0]
    assert tables == {"vg_sessions_recent", "vg_sessions_archive", "vg_sessions_trash", "vg_verdicts",
                      "vg_feedback", "vg_archive_cycles", "vg_schema_migrations"}


@needs_db
def test_first_300_stay_recent_and_600_is_the_cap(db):
    s = StorageSettings(database_url=DB_URL)
    first = insert(db, 100, T0)                                   # the oldest 100
    insert(db, 550, T0 + 60)
    out = repository.run_maintenance(db, s, T0 + 120)
    assert counts(db) == {"recent": 300, "archive": 300, "trash": 50}
    assert out["recent_overflow"] == 350 and out["capacity"] == 50
    with db.cursor() as cur:
        cur.execute("SELECT session_uid, trash_reason, trashed_from FROM vg_sessions_trash")
        trashed = cur.fetchall()
        cur.execute("SELECT MIN(created_at) FROM vg_sessions_recent")
        newest_kept = cur.fetchone()[0]
    db.commit()
    assert {u for u, _, _ in trashed} <= set(first)               # oldest first
    assert {(r, f) for _, r, f in trashed} == {("capacity", "archive")}
    assert newest_kept.timestamp() > 0


@needs_db
def test_calls_move_to_archive_after_5_days_and_trash_after_10(db):
    s = StorageSettings(database_url=DB_URL, archive_reset_days=1000)   # isolate ageing from the reset
    uid = insert(db, 1, T0)[0]
    repository.run_maintenance(db, s, T0 + 4.9 * DAY)
    assert counts(db) == {"recent": 1, "archive": 0, "trash": 0}
    repository.run_maintenance(db, s, T0 + 5.1 * DAY)
    assert counts(db) == {"recent": 0, "archive": 1, "trash": 0}
    out = repository.run_maintenance(db, s, T0 + 10.1 * DAY)
    assert counts(db) == {"recent": 0, "archive": 0, "trash": 1} and out["expired"] == 1
    assert repository.get_call(db, uid)["trash_reason"] == "expired"


@needs_db
def test_archive_resets_into_the_trash_every_5_days(db):
    s = StorageSettings(database_url=DB_URL, recent_rows=2)
    repository.run_maintenance(db, s, T0)                          # opens cycle 1 at T0
    insert(db, 5, T0 + 60)
    repository.run_maintenance(db, s, T0 + DAY)
    assert counts(db) == {"recent": 2, "archive": 3, "trash": 0}
    out = repository.run_maintenance(db, s, T0 + 5 * DAY + 120)
    assert out["archive_reset"] == 3
    with db.cursor() as cur:
        cur.execute("SELECT cycle_id, reset_at IS NOT NULL, rows_trashed FROM vg_archive_cycles ORDER BY cycle_id")
        cycles = cur.fetchall()
    db.commit()
    assert cycles == ((1, 1, 3), (2, 0, 0))
    # The 2 recent calls aged past 5 days in the same run and start the new cycle's archive.
    assert counts(db) == {"recent": 0, "archive": 2, "trash": 3}


@needs_db
def test_trash_is_purged_with_verdict_and_feedback(db):
    s = StorageSettings(database_url=DB_URL, archive_reset_days=1000)
    uid = insert(db, 1, T0)[0]
    assert repository.save_feedback(db, uid, hash_client_id(CLIENT), False, "scam", "it was", T0) == "created"
    assert repository.trash_call(db, uid, hash_client_id(CLIENT), T0 + 60) == "ok"
    repository.run_maintenance(db, s, T0 + 9 * DAY)
    assert counts(db)["trash"] == 1
    out = repository.run_maintenance(db, s, T0 + 10.5 * DAY)
    assert out["purged"] == 1 and counts(db)["trash"] == 0
    with db.cursor() as cur:
        cur.execute("SELECT (SELECT COUNT(*) FROM vg_verdicts), (SELECT COUNT(*) FROM vg_feedback)")
        assert cur.fetchone() == (0, 0)
    db.commit()


@needs_db
def test_trash_cap_purges_the_oldest(db):
    s = StorageSettings(database_url=DB_URL, max_live_rows=10, recent_rows=10, max_trash_rows=5)
    insert(db, 20, T0)
    repository.run_maintenance(db, s, T0 + 60)
    assert counts(db) == {"recent": 10, "archive": 0, "trash": 5}


@needs_db
def test_restore_from_trash(db):
    s = StorageSettings(database_url=DB_URL)
    uid = insert(db, 1, T0)[0]
    assert repository.trash_call(db, uid, hash_client_id("someone-else-99"), T0) == "forbidden"
    assert repository.trash_call(db, uid, hash_client_id(CLIENT), T0) == "ok"
    assert repository.restore_call(db, uid, s, T0 + DAY) == "ok"
    assert counts(db) == {"recent": 1, "archive": 0, "trash": 0}
    repository.trash_call(db, uid, hash_client_id(CLIENT), T0 + DAY)
    assert repository.restore_call(db, uid, s, T0 + 11 * DAY) == "expired"


@needs_db
def test_live_session_and_upload_are_stored_and_served(db, make_client):
    from tests.helpers import wav_bytes

    clock = Clock(time.time())
    storage = Storage(StorageSettings(database_url=DB_URL, admin_token=ADMIN, maintenance_seconds=3600), clock=clock)
    client, _, _ = make_client(texts=["read me the OTP now", "hello"], storage=storage)
    headers = {"X-VoiceGuard-Client": CLIENT}
    with client.websocket_connect(f"/ws/live-1?source=tab&consent=1&client={CLIENT}") as ws:
        ws.receive_json()
        send_audio(ws, tone(2.5))
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json(), ws.receive_json()
    r = client.post("/analyze-file?consent=1", headers=headers,
                    files={"file": ("call.wav", wav_bytes(tone(5.0)), "audio/wav")})
    assert r.status_code == 200
    assert storage.flush() and storage.status()["written"] == 2

    page = client.get("/api/history", headers=headers).json()
    assert [i["source"] for i in page["items"]] == ["file", "tab"]
    upload, live = page["items"]
    assert upload["file_name"] == "call.wav" and live["reasons"] and live["tier"] == "recent"

    detail = client.get(f"/api/history/{live['session_uid']}", headers=headers).json()
    assert detail["consent_given"] == 1 and detail["input_sample_rate"] == 16000
    assert detail["verdict"]["window_summary"] and detail["verdict"]["transcript"] is None
    assert "client_hash" not in detail
    other = {"X-VoiceGuard-Client": "another-browser-1"}
    assert client.get(f"/api/history/{live['session_uid']}", headers=other).status_code == 404
    assert client.get("/api/history", headers=other).json()["items"] == []
    assert client.get("/api/history").json()["code"] == "missing_client_id"

    fb = client.post(f"/api/history/{live['session_uid']}/feedback", headers=headers,
                     json={"is_correct": False, "actual_label": "scam"})
    assert fb.json() == {"session_uid": live["session_uid"], "status": "created"}
    fb = client.post(f"/api/history/{live['session_uid']}/feedback", headers=headers, json={"is_correct": True})
    assert fb.json()["status"] == "updated"
    assert client.post(f"/api/history/{live['session_uid']}/feedback", headers=headers,
                       json={"is_correct": "maybe"}).status_code == 422

    assert client.delete(f"/api/history/{upload['session_uid']}", headers=other).status_code == 404
    assert client.delete(f"/api/history/{upload['session_uid']}", headers=headers).json()["status"] == "trashed"
    status = client.get("/api/storage/status").json()
    assert status["counts"]["recent"] == 1 and status["counts"]["trash"] == 1 and status["writer"]["state"] == "ready"

    assert client.post("/api/storage/maintenance").status_code == 403
    assert client.post("/api/storage/maintenance", headers={"X-Admin-Token": ADMIN}).status_code == 200
    restore = client.post(f"/api/storage/trash/{upload['session_uid']}/restore", headers={"X-Admin-Token": ADMIN})
    assert restore.json()["status"] == "restored"


@needs_db
def test_cleared_or_empty_sessions_are_not_stored(db, make_client):
    storage = Storage(StorageSettings(database_url=DB_URL))
    client, _, _ = make_client(storage=storage)
    with client.websocket_connect(f"/ws/empty?client={CLIENT}") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert storage.flush() and storage.status()["written"] == 0


@needs_db
def test_writer_batches_many_calls_quickly(db):
    storage = Storage(StorageSettings(database_url=DB_URL, maintenance_seconds=3600))
    storage.start()
    try:
        deadline = time.monotonic() + 10
        while storage.state != "ready" and time.monotonic() < deadline:
            time.sleep(0.02)
        started = time.perf_counter()
        for _ in range(500):
            assert storage.submit(record(ended_at=time.time()))
        submit_ms = (time.perf_counter() - started) * 1000
        assert storage.flush(30)
        assert storage.status()["written"] == 500
        assert submit_ms < 250                                    # submitting never touches the network
    finally:
        storage.stop()
    assert counts(_conn())["recent"] == 500
