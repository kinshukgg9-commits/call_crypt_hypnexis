"""VoiceGuard API: one FastAPI process serving the REST API, the live WebSocket and the UI.

Run:  uvicorn backend.app:app --host 0.0.0.0 --port 8000 --workers 1
(one worker: models and sessions live in process memory)
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Header, Query, Request, UploadFile, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from backend import __version__
from backend.audio_io import AudioDecodeError, decode_audio_bytes
from backend.config import Settings
from backend.messages import MESSAGES
from backend.pipeline import Analyzer, summarize
from backend.schemas import (
    AudioConfig,
    ConfigResponse,
    ErrorResponse,
    LimitConfig,
    ModelStatus,
    StatusResponse,
    ThresholdConfig,
    UploadAnalysis,
)
from backend.session import (
    CLOSE_BAD_REQUEST,
    CLOSE_BUSY,
    CLOSE_DUPLICATE,
    CLOSE_INTERNAL,
    SESSION_ID_RE,
    StreamSession,
    parse_sample_rate,
)
from backend.storage import Storage, StorageSettings, record_stream, record_upload
from backend.storage.api import build_router

log = logging.getLogger("voiceguard")

RECOMMENDED_FRAME_MS = 128
ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Empty or malformed upload"},
    413: {"model": ErrorResponse, "description": "File or audio too large"},
    415: {"model": ErrorResponse, "description": "Not a decodable audio file"},
    422: {"model": ErrorResponse, "description": "No audio in the file / invalid request"},
}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code, self.code, self.detail = status_code, code, detail


def _error(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code}, status_code=status_code)


class UploadSizeLimit:
    """Rejects oversized uploads from Content-Length before the body is read."""

    def __init__(self, app: ASGIApp, path: str, max_bytes: int):
        self.app, self.path, self.max_bytes = app, path, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == self.path and scope["method"] == "POST":
            length = dict(scope["headers"]).get(b"content-length", b"")
            if length.isdigit() and int(length) > self.max_bytes:
                mb = self.max_bytes / (1024 * 1024)
                await _error(413, "file_too_large", f"Files must be at most {mb:.0f} MB.")(scope, receive, send)
                return
        await self.app(scope, receive, send)


class FrontendFiles(StaticFiles):
    """The static UI, mounted at "/". Unknown WebSocket paths are refused cleanly."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "unknown path"})
            return
        await super().__call__(scope, receive, send)

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        # Always revalidate (cheap 304s via ETag) so a redeploy never leaves browsers
        # running an old client against a new backend.
        response.headers["cache-control"] = "no-cache"
        return response


def _cors_options(origins: tuple[str, ...]) -> dict:
    """"*" allows every origin; an entry like https://*.vercel.app allows matching subdomains."""
    if "*" in origins:
        return {"allow_origins": ["*"]}
    options: dict = {"allow_origins": [o for o in origins if "*" not in o]}
    wildcards = [re.escape(o).replace(r"\*", "[A-Za-z0-9.-]+") for o in origins if "*" in o]
    if wildcards:
        options["allow_origin_regex"] = "|".join(wildcards)
    return options


def _configure_logging(level: str) -> None:
    logger = logging.getLogger("voiceguard")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False


def create_app(
    settings: Settings | None = None, analyzer: Analyzer | None = None, storage: Storage | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    _configure_logging(settings.log_level)
    analyzer = analyzer or Analyzer.from_settings(settings)
    storage = storage or Storage(StorageSettings.from_env())
    sessions: dict[str, StreamSession] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.preload_models:
            analyzer.preload_in_background()
        storage.start()                     # connects in the background: never delays start-up
        log.info(
            "VoiceGuard %s up: %.1f s windows, max %d sessions, transcription=%s, anti-spoof=%s",
            __version__, settings.window_seconds, settings.max_sessions,
            analyzer.model_status()["transcription"]["detail"], analyzer.model_status()["anti_spoof"]["detail"],
        )
        yield
        for session in list(sessions.values()):
            await session.close(1001, "server shutting down")
        await run_in_threadpool(storage.stop)

    app = FastAPI(
        title="VoiceGuard API",
        version=__version__,
        description=(
            "Live scam-cue and synthetic-voice risk indicators. Detection outputs are risk "
            "indicators, not proof. The live stream protocol (WebSocket /ws/{session_id}) is "
            "documented in docs/API.md."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.analyzer = analyzer
    app.state.sessions = sessions
    app.state.storage = storage

    # Order matters: CORS is added last so it is outermost and also wraps the 413 response.
    app.add_middleware(UploadSizeLimit, path="/analyze-file", max_bytes=settings.max_upload_bytes + 64 * 1024)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            **_cors_options(settings.cors_origins),
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],   # DELETE: removing a call from history
            allow_headers=["*"],
            max_age=600,
        )

    # Every HTTP error has the same shape: {"detail": str, "code": str}
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        response = _error(exc.status_code, f"http_{exc.status_code}", str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = f"{where}: {first.get('msg', 'invalid request')}" if where else "Invalid request."
        return _error(422, "invalid_request", message)

    # -- REST -----------------------------------------------------------------------
    storage_privacy = (
        "Audio is streamed to the VoiceGuard server for analysis, held in memory only while this session is "
        "open, and never stored. The result of each check (verdict, scores and matched tactics, without audio "
        f"or transcript) is kept for {storage.settings.trash_after_days:g} days, then moved to a deletion bin "
        f"and erased within {storage.settings.trash_retention_days:g} more days."
    )
    if storage.settings.enabled:
        app.include_router(build_router(storage, ApiError))

    @app.get("/health", tags=["system"])
    def health() -> dict:
        """Liveness probe for container platforms."""
        return {"ok": True}

    @app.head("/health", include_in_schema=False)
    def health_head() -> dict:
        return {"ok": True}

    @app.get("/api/status", response_model=StatusResponse, tags=["system"])
    def status() -> StatusResponse:
        """Model readiness and session load. Models load in the background after startup."""
        return StatusResponse(
            ok=True,
            version=__version__,
            active_sessions=len(sessions),
            max_sessions=settings.max_sessions,
            models={name: ModelStatus(**st) for name, st in analyzer.model_status().items()},
        )

    @app.get("/api/config", response_model=ConfigResponse, tags=["system"])
    def config() -> ConfigResponse:
        """Everything the frontend needs to talk to this server, plus the user-facing copy."""
        return ConfigResponse(
            version=__version__,
            audio=AudioConfig(
                sample_rate=settings.sample_rate,
                window_seconds=settings.window_seconds,
                recommended_frame_ms=RECOMMENDED_FRAME_MS,
                min_input_sample_rate=settings.min_input_sample_rate,
                max_input_sample_rate=settings.max_input_sample_rate,
            ),
            thresholds=ThresholdConfig(
                scam_verdict=settings.verdict_scam_threshold,
                risk_high=settings.risk_high, risk_medium=settings.risk_medium,
                voice=settings.voice_threshold, voice_share=settings.voice_min_share,
                voice_strong=settings.voice_strong,
            ),
            limits=LimitConfig(
                max_upload_mb=settings.max_upload_mb,
                max_upload_seconds=settings.max_upload_seconds,
                max_frame_bytes=settings.max_frame_bytes,
                idle_timeout_seconds=settings.idle_timeout_seconds,
                max_sessions=settings.max_sessions,
            ),
            messages={**MESSAGES, "privacy": storage_privacy} if storage.settings.enabled else MESSAGES,
            endpoints={
                "websocket": "/ws/{session_id}",
                "upload": "/analyze-file",
                "health": "/health",
                "status": "/api/status",
                "config": "/api/config",
                "docs": "/docs",
            },
        )

    @app.post("/analyze-file", response_model=UploadAnalysis, responses=ERROR_RESPONSES, tags=["analysis"])
    async def analyze_file(
        file: UploadFile = File(..., description="Audio file: WAV, MP3, M4A, OGG, FLAC, WEBM ..."),
        consent: str | None = Query(None, description="1 if the user ticked the consent box (stored with the result)."),
        x_voiceguard_client: str | None = Header(None),
    ) -> UploadAnalysis:
        """Analyse a recorded clip and return one verdict for the whole recording. The audio is never
        stored; when a database is configured, the verdict and file details are."""
        started_at = time.time()
        try:
            raw = await file.read(settings.max_upload_bytes + 1)
        finally:
            await file.close()
        if len(raw) > settings.max_upload_bytes:
            raise ApiError(413, "file_too_large", f"Files must be at most {settings.max_upload_mb:g} MB.")
        if not raw:
            raise ApiError(400, "empty_file", "The uploaded file is empty.")
        try:
            audio = await run_in_threadpool(decode_audio_bytes, raw, settings.sample_rate)
        except AudioDecodeError as exc:
            raise ApiError(415, "unsupported_audio", str(exc)) from None
        size_bytes = len(raw)
        del raw
        if audio.size == 0:
            raise ApiError(422, "no_audio", "No audio could be decoded from this file.")
        duration = audio.size / settings.sample_rate
        if duration > settings.max_upload_seconds:
            raise ApiError(
                413, "audio_too_long", f"Audio must be at most {settings.max_upload_seconds:g} s (got {duration:.0f} s)."
            )
        session_id = f"upload-{uuid.uuid4().hex[:8]}"
        results, verdict = await run_in_threadpool(analyzer.analyze_clip, audio, session_id)
        log.info("upload %s analysed: %.1f s, %d windows, verdict=%s", session_id, duration, len(results), verdict.verdict)
        record_upload(
            storage, session_id=session_id, results=results, verdict=verdict, duration=duration,
            filename=file.filename, content_type=file.content_type, size_bytes=size_bytes,
            started_at=started_at, client_id=x_voiceguard_client, consent=consent,
        )
        return UploadAnalysis(
            session_id=session_id,
            filename=file.filename,
            duration=round(duration, 3),
            window_seconds=settings.window_seconds,
            verdict=verdict,
            results=results,
            summary=summarize(results),
        )

    # -- live stream ------------------------------------------------------------------
    @app.websocket("/ws/{session_id}")
    async def ws_endpoint(ws: WebSocket, session_id: str) -> None:
        await ws.accept()

        async def reject(code: str, message: str, close_code: int) -> None:
            try:
                await ws.send_text(json.dumps({"type": "error", "code": code, "message": message, "fatal": True}))
                await ws.close(code=close_code, reason=code)
            except Exception:
                pass

        if not SESSION_ID_RE.match(session_id):
            return await reject(
                "invalid_session_id", "Session ids must be 1-64 letters, digits, '-' or '_'.", CLOSE_BAD_REQUEST
            )
        rate = parse_sample_rate(
            ws.query_params.get("sample_rate"),
            settings.min_input_sample_rate, settings.max_input_sample_rate, settings.sample_rate,
        )
        if rate is None:
            return await reject(
                "invalid_sample_rate",
                f"sample_rate must be an integer from {settings.min_input_sample_rate} "
                f"to {settings.max_input_sample_rate}.",
                CLOSE_BAD_REQUEST,
            )
        if session_id in sessions:
            return await reject("session_active", "This session id is already streaming.", CLOSE_DUPLICATE)
        if len(sessions) >= settings.max_sessions:
            return await reject("server_busy", "Too many live sessions; try again shortly.", CLOSE_BUSY)

        session = StreamSession(ws, session_id, analyzer, rate)
        sessions[session_id] = session
        log.info("session %s opened (%d Hz input, %d active)", session_id, rate, len(sessions))
        try:
            await session.run()
        except Exception:
            log.exception("session %s failed", session_id)
            await session.send_error("internal_error", "The server hit an unexpected error.", fatal=True)
            await session.close(CLOSE_INTERNAL, "internal error")
        finally:
            sessions.pop(session_id, None)
            record_stream(storage, session, ws.query_params)
            log.info(
                "session %s closed (%d windows analysed, %d dropped)", session_id, session.analysed, session.dropped
            )

    # -- UI (keep LAST: it catches every remaining path) ---------------------------------
    if settings.frontend_dir.is_dir():
        app.mount("/", FrontendFiles(directory=settings.frontend_dir, html=True), name="ui")
    else:
        @app.get("/", include_in_schema=False)
        def root() -> dict:
            return {"name": "VoiceGuard API", "version": __version__, "docs": "/docs"}

    return app


app = create_app()
