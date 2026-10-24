"""The storage service: a background writer thread plus a tiny pool of reader connections.

Why a writer thread: a WebSocket or upload must never wait for the database. `submit()` only
puts the record on an in-memory queue (microseconds); the writer drains it in batches of up to
DB_BATCH_SIZE calls per transaction over one long-lived connection, and runs the lifecycle job
every DB_MAINTENANCE_SECONDS. If the database is down, records wait in the queue (the oldest is
dropped beyond DB_QUEUE_SIZE) and the writer reconnects with backoff. Analysis never fails
because of the database.

Why long-lived connections: opening a TLS connection to a hosted MySQL costs 100-300 ms; reusing
one costs a ping. Memory: PyMySQL is pure Python, a few MB in all.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from backend.storage import repository
from backend.storage.config import StorageSettings
from backend.storage.records import CallRecord
from backend.storage.schema import migrate

log = logging.getLogger("voiceguard.storage")

_POOL_SIZE = 2


class StorageUnavailable(Exception):
    pass


def _alive(conn):
    """The connection if a ping gets through, else None (closed). Idle hosted MySQL drops connections."""
    if conn is None:
        return None
    try:
        conn.ping(False)
        return conn
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return None


class Storage:
    def __init__(self, settings: StorageSettings, clock: Callable[[], float] = time.time,
                 connect: Callable[..., Any] | None = None):
        self.settings = settings
        self.clock = clock
        self._connect_fn = connect
        self._queue: queue.Queue[CallRecord] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool: list[Any] = []
        self._pool_lock = threading.Lock()
        self._pool_sem = threading.BoundedSemaphore(_POOL_SIZE)
        self.state = "disabled" if not settings.enabled else "not_started"
        self.error: str | None = None
        self.written = 0
        self.dropped = 0
        self.last_maintenance: dict[str, Any] | None = None

    # -- connections ----------------------------------------------------------------
    def _connect(self):
        if self._connect_fn:
            return self._connect_fn()
        import pymysql                                    # imported lazily: storage is optional

        return pymysql.connect(**self.settings.connect_kwargs())

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """A pooled reader connection, checked with a ping (which reconnects if it dropped)."""
        if self.state != "ready":
            raise StorageUnavailable(self.error or "The database is not connected.")
        with self._pool_sem:
            with self._pool_lock:
                conn = self._pool.pop() if self._pool else None
            try:
                conn = _alive(conn) or self._connect()
            except Exception as exc:
                raise StorageUnavailable(f"Database connection failed: {exc}") from None
            ok = False
            try:
                yield conn
                ok = True
            finally:
                if not ok:
                    try:
                        conn.rollback()
                    except Exception:
                        conn = None
                if conn is not None:
                    with self._pool_lock:
                        self._pool.append(conn)

    # -- lifecycle -----------------------------------------------------------------
    def start(self) -> None:
        """Starts the writer thread. Returns at once: connecting never delays server start-up."""
        if not self.settings.enabled or self._thread:
            return
        self.state = "connecting"
        self._thread = threading.Thread(target=self._run, name="voiceguard-db-writer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Writes what is still queued (for up to `timeout`), then closes every connection."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None
        with self._pool_lock:
            pool, self._pool = self._pool, []
        for conn in pool:
            try:
                conn.close()
            except Exception:
                pass
        if self.settings.enabled:
            self.state = "stopped"

    def submit(self, record: CallRecord) -> bool:
        """Queues one call for writing. Never blocks; returns False if storage is off."""
        if not self.settings.enabled or self._stop.is_set():
            return False
        while self._queue.qsize() >= self.settings.queue_size:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self.dropped += 1
                log.warning("database queue full: dropped the oldest unsaved call")
            except queue.Empty:
                break
        self._queue.put_nowait(record)
        return True

    def flush(self, timeout: float = 10.0) -> bool:
        """Waits until everything queued so far is written (used by tests and shutdown)."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self._queue.unfinished_tasks

    def maintain_now(self) -> dict[str, int] | None:
        with self.connection() as conn:
            result = repository.run_maintenance(conn, self.settings, self.clock())
        if result is not None:
            self.last_maintenance = {"at": self.clock(), **result}
        return result

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "error": self.error,
            "queued": self._queue.qsize(),
            "written": self.written,
            "dropped": self.dropped,
            "last_maintenance": self.last_maintenance,
        }

    # -- writer thread ---------------------------------------------------------------
    def _run(self) -> None:
        conn = None
        backoff = 1.0
        next_maintenance = 0.0
        while True:
            stopping = self._stop.is_set()
            if stopping and not self._queue.unfinished_tasks:
                break
            if conn is None:
                try:
                    conn = self._connect()
                    migrate(conn)
                    self.state, self.error, backoff = "ready", None, 1.0
                    log.info("database connected")
                except Exception as exc:
                    conn = None
                    self.state, self.error = "error", f"{type(exc).__name__}: {exc}"
                    log.warning("database unavailable (%s); retrying in %.0f s", self.error, backoff)
                    if stopping or self._stop.wait(backoff):
                        break
                    backoff = min(backoff * 2, 60.0)
                    continue
            try:
                if time.monotonic() >= next_maintenance:
                    result = repository.run_maintenance(conn, self.settings, self.clock())
                    if result is not None:
                        self.last_maintenance = {"at": self.clock(), **result}
                        if any(result.values()):
                            log.info("database maintenance: %s", result)
                    next_maintenance = time.monotonic() + self.settings.maintenance_seconds
                batch = self._take_batch(min(1.0, max(0.0, next_maintenance - time.monotonic())))
                if batch:
                    try:
                        conn = _alive(conn) or self._connect()
                        repository.insert_records(conn, batch, self.clock())
                        self.written += len(batch)
                    except Exception:
                        for record in batch:                       # put them back for the retry
                            self._queue.put_nowait(record)
                        raise
                    finally:
                        for _ in batch:
                            self._queue.task_done()
            except Exception as exc:
                self.state, self.error = "error", f"{type(exc).__name__}: {exc}"
                log.exception("database write failed; reconnecting")
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                if self._stop.wait(backoff):
                    break
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _take_batch(self, wait: float) -> list[CallRecord]:
        try:
            batch = [self._queue.get(timeout=wait) if wait > 0 else self._queue.get_nowait()]
        except queue.Empty:
            return []
        while len(batch) < self.settings.batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch
