"""Storage settings, read once from environment variables (same style as backend/config.py).

Storage is off unless DATABASE_URL is set, so the app and its tests keep working with no
database at all. Example:

    DATABASE_URL=mysql://voiceguard:secret@db.example.com:3306/voiceguard?ssl=true
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

from backend.config import _bool, _float, _int, _str


@dataclass(frozen=True)
class StorageSettings:
    database_url: str | None = None

    # Record lifecycle (see backend/storage/lifecycle.py)
    recent_rows: int = 300              # vg_sessions_recent holds at most this many calls
    recent_days: float = 5.0            # ... and none older than this
    max_live_rows: int = 600            # recent + archive together; the oldest are evicted beyond it
    archive_reset_days: float = 5.0     # vg_sessions_archive is emptied into the trash this often
    trash_after_days: float = 10.0      # any call older than this goes to the trash
    trash_retention_days: float = 10.0  # the trash is purged (for good) after this
    max_trash_rows: int = 600
    maintenance_seconds: float = 300.0  # how often the lifecycle job runs

    # Writing
    queue_size: int = 500               # records waiting for the database, in memory
    batch_size: int = 50                # records written per transaction
    connect_timeout: float = 5.0
    store_transcripts: bool = False     # transcripts can hold OTPs and names: off by default
    admin_token: str | None = None      # guards the maintenance / restore endpoints

    @property
    def enabled(self) -> bool:
        return bool(self.database_url)

    def connect_kwargs(self) -> dict:
        """PyMySQL keyword arguments parsed from DATABASE_URL."""
        url = urlparse(self.database_url or "")
        if url.scheme not in ("mysql", "mysql+pymysql"):
            raise ValueError("DATABASE_URL must start with mysql://")
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        kwargs = {
            "host": url.hostname or "localhost",
            "port": url.port or 3306,
            "user": unquote(url.username or ""),
            "password": unquote(url.password or ""),
            "database": url.path.lstrip("/"),
            "charset": "utf8mb4",
            "connect_timeout": self.connect_timeout,
            "read_timeout": 15,
            "write_timeout": 15,
            "autocommit": False,
        }
        if not kwargs["database"]:
            raise ValueError("DATABASE_URL must name a database, e.g. mysql://user:pass@host/voiceguard")
        if query.get("ssl", "").lower() in ("1", "true", "required"):
            # Managed MySQL hosts require TLS; the system CA bundle verifies them.
            kwargs.update(ssl_verify_cert=True, ssl_verify_identity=True, ssl_ca=query.get("ssl_ca"))
        return kwargs

    @classmethod
    def from_env(cls) -> StorageSettings:
        return cls(
            database_url=_str("DATABASE_URL", None),
            recent_rows=_int("DB_RECENT_ROWS", 300, 1, 1_000_000),
            recent_days=_float("DB_RECENT_DAYS", 5.0, 0.001, 3650.0),
            max_live_rows=_int("DB_MAX_ROWS", 600, 1, 10_000_000),
            archive_reset_days=_float("DB_ARCHIVE_RESET_DAYS", 5.0, 0.001, 3650.0),
            trash_after_days=_float("DB_TRASH_AFTER_DAYS", 10.0, 0.001, 3650.0),
            trash_retention_days=_float("DB_TRASH_RETENTION_DAYS", 10.0, 0.0, 3650.0),
            max_trash_rows=_int("DB_MAX_TRASH_ROWS", 600, 0, 10_000_000),
            maintenance_seconds=_float("DB_MAINTENANCE_SECONDS", 300.0, 5.0, 86400.0),
            queue_size=_int("DB_QUEUE_SIZE", 500, 1, 100_000),
            batch_size=_int("DB_BATCH_SIZE", 50, 1, 10_000),
            connect_timeout=_float("DB_CONNECT_TIMEOUT", 5.0, 0.5, 120.0),
            store_transcripts=_bool("DB_STORE_TRANSCRIPTS", False),
            admin_token=_str("STORAGE_ADMIN_TOKEN", None),
        )
