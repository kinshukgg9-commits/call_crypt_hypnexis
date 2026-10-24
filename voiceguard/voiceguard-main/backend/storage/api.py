"""REST endpoints for stored calls. Mounted by backend/app.py only when DATABASE_URL is set.

There are no accounts in VoiceGuard, so each browser sends a random id it keeps in localStorage
as the X-VoiceGuard-Client header. The server stores only its SHA-256, and a browser only ever
sees (and can delete or give feedback on) its own calls.

  GET    /api/history                     this browser's calls, newest first
  GET    /api/history/{session_uid}       one call with its verdict and feedback
  DELETE /api/history/{session_uid}       move one of this browser's calls to the trash
  POST   /api/history/{session_uid}/feedback   {"is_correct": bool, "actual_label": ..., "comment": ...}
  GET    /api/storage/status              table sizes, archive cycle, writer queue
  POST   /api/storage/maintenance         run the lifecycle job now          (X-Admin-Token)
  POST   /api/storage/trash/{uid}/restore move a call back out of the trash  (X-Admin-Token)
"""
from __future__ import annotations

import hmac
import re
from typing import Any, Literal

from fastapi import APIRouter, Header, Path, Query
from pydantic import BaseModel, Field

from backend.storage import repository
from backend.storage.records import hash_client_id
from backend.storage.store import Storage, StorageUnavailable

UID_PATTERN = r"^[0-9a-f]{32}$"
_UID_RE = re.compile(UID_PATTERN)


class FeedbackIn(BaseModel):
    is_correct: bool = Field(description="Was VoiceGuard's result right?")
    actual_label: Literal["safe", "ai_voice", "scam", "unsure"] | None = Field(
        default=None, description="What the call really was, if the user knows."
    )
    comment: str | None = Field(default=None, max_length=500)


class FeedbackOut(BaseModel):
    session_uid: str
    status: Literal["created", "updated"]


class HistoryItem(BaseModel):
    session_uid: str
    tier: Literal["recent", "archive"]
    source: str
    file_name: str | None
    audio_seconds: float
    started_at: str | None
    ended_at: str | None
    created_at: str
    end_reason: str
    verdict: str | None
    title: str | None
    scam_score: float | None
    ai_voice: bool | None
    voice_score: float | None
    reasons: list[str]
    has_feedback: bool


class HistoryPage(BaseModel):
    items: list[HistoryItem]
    limit: int
    offset: int


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k != "client_hash"}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="milliseconds") + "Z"
    return value


def build_router(storage: Storage, api_error: type[Exception]) -> APIRouter:
    """`api_error(status, code, detail)` is backend.app.ApiError, so errors keep the app's {detail, code} shape."""
    router = APIRouter(tags=["storage"])
    s = storage.settings

    def client(raw: str | None) -> str:
        hashed = hash_client_id(raw)
        if hashed is None:
            raise api_error(400, "missing_client_id",
                            "Send X-VoiceGuard-Client: an 8-64 character id made of letters, digits, '-' or '_'.")
        return hashed

    def admin(token: str | None) -> None:
        if not s.admin_token:
            raise api_error(403, "admin_disabled", "Set STORAGE_ADMIN_TOKEN on the server to use this endpoint.")
        if not token or not hmac.compare_digest(token, s.admin_token):
            raise api_error(403, "forbidden", "Wrong or missing X-Admin-Token.")

    db = storage.connection                  # raises StorageUnavailable on entry; endpoints turn it into a 503

    # Plain `def` endpoints: FastAPI runs them in its thread pool, so a slow query never blocks
    # the event loop that carries live audio.
    @router.get("/api/history", response_model=HistoryPage)
    def history(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10_000),
        x_voiceguard_client: str | None = Header(None),
    ) -> HistoryPage:
        """This browser's stored calls (recent and archive), newest first."""
        hashed = client(x_voiceguard_client)
        try:
            with db() as conn:
                rows = repository.list_history(conn, hashed, limit, offset)
        except StorageUnavailable as exc:
            raise api_error(503, "storage_unavailable", str(exc)) from None
        return HistoryPage(items=[HistoryItem(**r) for r in rows], limit=limit, offset=offset)

    @router.get("/api/history/{session_uid}")
    def history_item(
        session_uid: str = Path(pattern=UID_PATTERN),
        x_voiceguard_client: str | None = Header(None),
    ) -> dict:
        """One of this browser's calls, with its verdict, per-window scores and feedback."""
        hashed = client(x_voiceguard_client)
        try:
            with db() as conn:
                call = repository.get_call(conn, session_uid)
        except StorageUnavailable as exc:
            raise api_error(503, "storage_unavailable", str(exc)) from None
        if not call or call["tier"] == "trash" or call["client_hash"] != hashed:
            raise api_error(404, "not_found", "No such call in this browser's history.")
        call["feedback"] = [f for f in call["feedback"] if f["client_hash"] == hashed]
        return _jsonable(call)

    @router.delete("/api/history/{session_uid}")
    def delete_item(
        session_uid: str = Path(pattern=UID_PATTERN),
        x_voiceguard_client: str | None = Header(None),
    ) -> dict:
        """Moves one of this browser's calls to the trash (purged after DB_TRASH_RETENTION_DAYS)."""
        hashed = client(x_voiceguard_client)
        try:
            with db() as conn:
                outcome = repository.trash_call(conn, session_uid, hashed, storage.clock())
        except StorageUnavailable as exc:
            raise api_error(503, "storage_unavailable", str(exc)) from None
        if outcome != "ok":
            raise api_error(404, "not_found", "No such call in this browser's history.")
        return {"session_uid": session_uid, "status": "trashed"}

    @router.post("/api/history/{session_uid}/feedback", response_model=FeedbackOut)
    def feedback(
        body: FeedbackIn,
        session_uid: str = Path(pattern=UID_PATTERN),
        x_voiceguard_client: str | None = Header(None),
    ) -> FeedbackOut:
        """Saves (or replaces) this browser's feedback on a call's result."""
        hashed = client(x_voiceguard_client)
        comment = (body.comment or "").strip() or None
        try:
            with db() as conn:
                status = repository.save_feedback(
                    conn, session_uid, hashed, body.is_correct, body.actual_label, comment, storage.clock()
                )
        except StorageUnavailable as exc:
            raise api_error(503, "storage_unavailable", str(exc)) from None
        if status is None:
            raise api_error(404, "not_found", "No such call (it may have expired).")
        return FeedbackOut(session_uid=session_uid, status=status)

    @router.get("/api/storage/status")
    def storage_status() -> dict:
        """Writer state, plus table sizes and the archive cycle when the database is reachable."""
        out: dict[str, Any] = {"writer": storage.status()}
        if storage.state == "ready":
            try:
                with storage.connection() as conn:
                    out.update(repository.stats(conn, s))
            except StorageUnavailable as exc:
                out["writer"]["error"] = str(exc)
        return out

    @router.post("/api/storage/maintenance")
    def maintenance(x_admin_token: str | None = Header(None)) -> dict:
        """Runs the lifecycle job now instead of waiting for the next scheduled run."""
        admin(x_admin_token)
        try:
            result = storage.maintain_now()
        except StorageUnavailable as exc:
            raise api_error(503, "storage_unavailable", str(exc)) from None
        if result is None:
            raise api_error(409, "maintenance_running", "Maintenance is already running; try again shortly.")
        return result

    @router.post("/api/storage/trash/{session_uid}/restore")
    def restore(session_uid: str = Path(pattern=UID_PATTERN), x_admin_token: str | None = Header(None)) -> dict:
        """Moves a call from the trash back to the recent table."""
        admin(x_admin_token)
        try:
            with db() as conn:
                outcome = repository.restore_call(conn, session_uid, s, storage.clock())
        except StorageUnavailable as exc:
            raise api_error(503, "storage_unavailable", str(exc)) from None
        if outcome == "not_found":
            raise api_error(404, "not_found", "No such call in the trash.")
        if outcome == "expired":
            raise api_error(409, "expired", f"Calls older than {s.trash_after_days:g} days can't be restored.")
        return {"session_uid": session_uid, "status": "restored"}

    return router
