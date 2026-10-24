# VoiceGuard storage: MySQL database and FastAPI layer

This covers the database added for the Duo A roadmap task "Design tables: sessions, verdicts,
feedback. No audio stored" and "Save each verdict and user feedback to the database".

Storage is **off unless `DATABASE_URL` is set**, so the app, the demo and the existing tests behave
exactly as before without a database.

## 1. What the site takes from the user, and what is stored

| Input on the site | Where it comes from | Stored? |
|---|---|---|
| Consent checkbox | `#consent` | Yes, `consent_given` (as the browser reports it) |
| Audio source: microphone, tab audio, upload | Start / Share tab audio / Upload buttons | Yes, `source` (`mic`, `tab`, `file`) |
| Live audio stream (PCM16) | microphone or shared tab | **Never** (kept in memory only, as before) |
| Uploaded recording | `#file` | **The audio never.** File name, type and size are |
| Sample rate of the stream | `?sample_rate=` | Yes, `input_sample_rate` |
| Session id | random per call | Yes, `client_session_id` |
| Stop / Clear | buttons | `end_reason`; Clear forgets the call, and a cleared call with no new audio is not stored |
| Run demo | `#demo` | No: the demo never talks to the server |
| "Was this result right?" | **not on the site yet**, endpoint ready | Yes, `vg_feedback` |
| Transcript | produced by the server | No, unless `DB_STORE_TRANSCRIPTS=true` (then with digit runs such as OTPs masked) |

Results produced by the server and stored per call: the call-level verdict (`safe`, `ai_voice`, `scam`,
`inconclusive`), headline and advice, scam score, matched tactics, AI-voice flag and share, windows,
speech seconds, languages, and a per-window summary (time, risk, scores, flags, tactics).

There are no accounts, so each browser keeps a random id in `localStorage` and sends it with each call
(`X-VoiceGuard-Client` header, or `?client=` on the WebSocket, where browsers can't set headers).
The server stores only its SHA-256 (`client_hash`), and a browser can only see its own history.

## 2. Tables

All tables are prefixed `vg_` and use InnoDB with utf8mb4. The SQL is in
[`database/voiceguard_schema.sql`](../database/voiceguard_schema.sql); the backend also creates the
tables by itself on first start.

| Table | What it holds |
|---|---|
| `vg_sessions_recent` | The newest calls: at most **300**, none older than **5 days** |
| `vg_sessions_archive` | Calls pushed out of recent. **Reset every 5 days** (its rows move to the trash) |
| `vg_sessions_trash` | Calls removed by age, capacity, archive reset or the user, with `trash_reason`; restorable until purged |
| `vg_verdicts` | One call-level result per call (`session_uid`) |
| `vg_feedback` | Feedback on a result, one per call per browser |
| `vg_archive_cycles` | One row per 5-day archive cycle, so the reset schedule survives restarts |
| `vg_schema_migrations` | Which schema migrations have run |

A call keeps the same `session_uid` in whichever `vg_sessions_*` table it is in, so its verdict and
feedback never move; only the session row does. The three session tables share their columns, and
moves are `INSERT ... SELECT` plus `DELETE` by primary key in one transaction, so a call is never in
two tables at once.

```
vg_sessions_recent ─┐
vg_sessions_archive ├── session_uid ──< vg_verdicts (1 per call)
vg_sessions_trash  ─┘               └─< vg_feedback (1 per call per browser)
```

## 3. Lifecycle

A maintenance job runs in the background every 5 minutes (`DB_MAINTENANCE_SECONDS`), and on demand via
`POST /api/storage/maintenance`. In order:

1. **Archive reset**: when the current 5-day cycle is over, every archive row moves to the trash
   (`archive_reset`) and a new cycle starts.
2. **Expiry**: any call older than **10 days** moves to the trash (`expired`).
3. **Ageing**: recent calls older than 5 days move to the archive.
4. **Recent cap**: beyond 300 recent calls, the oldest move to the archive.
5. **Capacity**: beyond **600** calls in recent + archive, the oldest move to the trash
   (`capacity`), archive first.
6. **Purge**: trash older than 10 days (`DB_TRASH_RETENTION_DAYS`), or beyond 600 rows, is deleted for
   good, with its verdict and feedback.

Every number is an environment variable (see [`database/env.example`](../database/env.example)).
If two server instances run, a MySQL `GET_LOCK` makes sure only one runs maintenance at a time.

## 4. Requirements that conflicted, and the bypass taken

1. **The 5-day archive reset and the 10-day trash conflict.** Calls reach the archive at 5 days old, and
   the archive is emptied every 5 days, so most calls leave before they are 10 days old and the 10-day
   rule would rarely apply. **Bypass:** the reset moves archive rows to the *trash* rather than deleting
   them, so nothing is lost before the trash is purged, and the 10-day expiry still runs as a safety net.
   Effect: a call is live for 5 to 10 days (never more than 10), then sits in the trash for up to 10
   more days, where it can be restored. To get exactly 10 days live, set `DB_ARCHIVE_RESET_DAYS=10`.
2. **"At least 600 ... if the data becomes less remove the old data."** Read as: keep up to 600 calls and,
   when room runs out, remove the oldest first. It is a cap of 600 (`DB_MAX_ROWS`), oldest out first.
3. **The trash had no limit.** Without one it would grow forever. It is purged after 10 days and capped
   at 600 rows.
4. **"512 MB bandwidth" is the server's memory, not bandwidth.** MySQL can't share that server: an idle
   MySQL 8.0 used 430 MB here, and VoiceGuard already peaks at about 425 MB of the 512 MB.
   **Bypass:** use a managed MySQL elsewhere (pick a region near the backend). Render's free disk is
   also wiped on every restart, so a database on it would lose its data anyway. The storage layer
   itself adds about 15 MB.
5. **The site promises that nothing is stored.** Storing results contradicts the old copy.
   **Bypass:** audio is still never stored and transcripts are off by default; only results and scores
   are kept. The consent text, the privacy dialog and `messages.privacy` now say so.
6. **The site has no accounts.** A history page would otherwise show everyone's calls. **Bypass:** the
   anonymous browser id described above. Clearing site data starts a new history.
7. **The site has no feedback input yet.** The endpoint and `client.sendFeedback()` are ready; the
   frontend duo needs to add the buttons (for example "Right" / "Wrong" under the result).
8. **The server could not see consent or the audio source.** The client now sends both. Consent is what
   the browser reports; the server can't verify it.
9. **Alembic (roadmap tool) was not used.** It brings SQLAlchemy (tens of MB on a 512 MB server). A
   small numbered-migration runner in `backend/storage/schema.py` does the same job for this schema.
10. **Connection time can't reach zero on the free plan.** Most of the wait is Render's free instance
    waking up after 15 idle minutes. See section 6.

## 5. Storage speed

- **Nothing waits for the database.** A finished call is put on an in-memory queue (about a
  microsecond); a background writer thread saves it. The WebSocket and upload paths never touch MySQL.
- **Batched writes**: up to 50 calls per transaction, as one multi-row `INSERT`. Measured on a local
  MySQL 8.0: 1.9 ms per call one at a time, 0.19 ms per call batched. Against a hosted database with a
  50 ms round trip, the gap grows with the network.
- **Long-lived connections**: the writer keeps one connection and the API a pool of two, checked with
  a ping (0.2 ms) instead of reconnecting (30 ms locally, 100 to 300 ms with TLS to a hosted MySQL).
- **Indexes** on `created_at`, `(client_hash, created_at)` and `trashed_at`: the queries the lifecycle
  and the history page run.
- **Outages are survived**: if MySQL is down, calls wait in the queue (up to 500, oldest dropped
  beyond that) and the writer reconnects with backoff. Analysis never fails because of the database.
- **Start-up is not delayed**: the database connects in the background after the server is up.

## 6. Faster connection to the server

Measured causes, from the code and the project description: the free Render instance sleeps after
15 idle minutes and takes a minute or more to wake; the backend region is far from Indian users; and
the page made two requests one after the other before it showed "Online".

| Change | File | Effect |
|---|---|---|
| Ping `/health` every 10 minutes | `.github/workflows/keep-warm.yml` | The server stays awake, so there is no cold start. Uses about 730 of the 750 free hours a month for one service |
| Region `singapore` | `render.yaml` | Shorter round trips from India for every request and the WebSocket. Render can't move an existing service: create a new one from the blueprint |
| Preconnect and early wake-up ping | `frontend/config.js` | DNS and TLS are done, and the server starts waking, before the app's scripts load |
| Database connects in the background | `backend/app.py` | Adding storage does not slow start-up |

GitHub can delay scheduled workflows and pauses them after 60 days without commits; an external
monitor such as cron-job.org or UptimeRobot pinging `/health` every 10 minutes works the same way. A paid
instance removes the sleep entirely. The 9 s analysis per window on the free CPU is a separate limit.

## 7. Setting it up

1. Create a MySQL 8 database (a managed MySQL with a free plan, such as Aiven for MySQL; check its
   current terms, and choose a region near the backend). For local work:
   `docker compose -f database/docker-compose.mysql.yml up -d`.
2. Optionally run `database/voiceguard_schema.sql` (MySQL Workbench or `mysql < ...`). The backend
   creates the tables itself if you skip this.
3. Set `DATABASE_URL=mysql://user:password@host:3306/voiceguard?ssl=true` (add `&ssl_ca=/path/ca.pem`
   if your host gives you a CA file) and `STORAGE_ADMIN_TOKEN` on the backend (Render > Environment).
4. `pip install -r requirements.txt` (adds `PyMySQL[rsa]`) and start the server as usual.
   `GET /api/storage/status` should show `"state": "ready"`.

## 8. Endpoints

| Method and path | Who | What |
|---|---|---|
| `GET /api/history?limit=20&offset=0` | browser (`X-VoiceGuard-Client`) | This browser's calls, newest first |
| `GET /api/history/{session_uid}` | browser | One call with verdict, window summary and its feedback |
| `DELETE /api/history/{session_uid}` | browser | Moves one of its calls to the trash |
| `POST /api/history/{session_uid}/feedback` | browser | `{"is_correct": true, "actual_label": "scam", "comment": "..."}` |
| `GET /api/storage/status` | anyone | Writer state, table sizes, archive cycle |
| `POST /api/storage/maintenance` | admin (`X-Admin-Token`) | Runs the lifecycle now |
| `POST /api/storage/trash/{session_uid}/restore` | admin | Moves a call from the trash back to recent |

Errors keep the app's usual shape `{"detail": "...", "code": "..."}`; `503 storage_unavailable` means
the database is not reachable. In the browser, `VoiceGuardClient` has `getHistory()`, `getCall()`,
`deleteCall()` and `sendFeedback()`, and sends consent, source and the browser id with every call.

## 9. Tests

```
VOICEGUARD_TEST_DATABASE_URL=mysql://user:pass@127.0.0.1:3306/voiceguard_test pytest tests/test_storage.py
```

The database tests drop every `vg_*` table in that database, so use a throw-away one. Without the
variable they are skipped, and the six tests that need no database still run. All 16 passed on MySQL
8.0.46 and MariaDB 10.11.
