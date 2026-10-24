"""Optional MySQL storage for analysed calls: tables in schema.py, lifecycle in repository.py,
non-blocking writes in store.py, REST endpoints in api.py. Off unless DATABASE_URL is set."""
from __future__ import annotations

import time
from typing import Any, Mapping

from backend import __version__
from backend.schemas import AnalysisResult, Verdict
from backend.storage.config import StorageSettings
from backend.storage.records import CallRecord, hash_client_id
from backend.storage.store import Storage, StorageUnavailable

__all__ = ["CallRecord", "Storage", "StorageSettings", "StorageUnavailable", "record_stream", "record_upload"]

_END_REASONS = {"stop": "stopped", "disconnect": "disconnected", "idle": "idle_timeout", "error": "error"}


def _consent(raw: str | None) -> bool | None:
    if raw is None:
        return None
    return raw.lower() in ("1", "true", "yes", "on")


def record_stream(storage: Storage, session: Any, params: Mapping[str, str]) -> bool:
    """Queues a finished live session. `params` are the WebSocket's query parameters
    (?source=mic|tab&consent=1&client=<browser id>): browsers can't set WebSocket headers."""
    if not storage.settings.enabled or not session.results:
        return False                                      # nothing analysed: nothing worth keeping
    record = CallRecord.from_results(
        session.results,
        store_transcript=storage.settings.store_transcripts,
        client_session_id=session.session_id,
        source=params.get("source") or "live",
        end_reason=_END_REASONS.get(session.end_reason, "error"),
        verdict=session.verdict.snapshot(final=True),
        started_at=session.started_at,
        ended_at=time.time(),
        client_hash=hash_client_id(params.get("client")),
        consent_given=_consent(params.get("consent")),
        input_sample_rate=session.input_rate,
        audio_seconds=session.results[-1].timestamp,
        windows_analysed=session.analysed,
        windows_dropped=session.dropped,
        app_version=__version__,
    )
    return storage.submit(record)


def record_upload(
    storage: Storage, *, session_id: str, results: list[AnalysisResult], verdict: Verdict, duration: float,
    filename: str | None, content_type: str | None, size_bytes: int, started_at: float,
    client_id: str | None, consent: str | None,
) -> bool:
    """Queues an analysed upload."""
    if not storage.settings.enabled:
        return False
    record = CallRecord.from_results(
        results,
        store_transcript=storage.settings.store_transcripts,
        client_session_id=session_id,
        source="file",
        end_reason="completed",
        verdict=verdict,
        started_at=started_at,
        ended_at=time.time(),
        client_hash=hash_client_id(client_id),
        consent_given=_consent(consent),
        file_name=filename,
        file_type=content_type,
        file_size_bytes=size_bytes,
        audio_seconds=duration,
        windows_analysed=len(results),
        app_version=__version__,
    )
    return storage.submit(record)
