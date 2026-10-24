"""One live WebSocket session: buffer PCM in RAM, analyse every window, send results.

Protocol (see docs/API.md):
  client -> server  binary frames: PCM16 little-endian mono at ?sample_rate= (default 16000)
                    text frames:   {"type": "ping"} | {"type": "clear"} | {"type": "stop"}
  server -> client  {"type": "ready"}, {"type": "result"}, {"type": "voice"}, {"type": "pong"},
                    {"type": "cleared"}, {"type": "stopped"}, {"type": "warning"}, {"type": "error"}

Every result carries the running call-level verdict; "stopped" carries the final one. Between
results, a "voice" message scores the latest 4 s of audio every VOICE_UPDATE_SECONDS (0.5 s).

Nothing is stored: the buffers and transcript context live in this object and
vanish when the socket closes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocket

from backend import __version__
from backend.pipeline import Analyzer
from backend.schemas import AnalysisResult
from ml.audio_preprocess import find_cut, pcm16_to_float32, resample

log = logging.getLogger("voiceguard.session")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Close codes (4000-4999 are application-defined)
CLOSE_NORMAL = 1000
CLOSE_TOO_BIG = 1009
CLOSE_INTERNAL = 1011
CLOSE_BAD_REQUEST = 4400
CLOSE_IDLE = 4408
CLOSE_DUPLICATE = 4409
CLOSE_BUSY = 4429


@dataclass
class _Window:
    audio: np.ndarray        # float32 at the model sample rate
    index: int
    timestamp: float         # seconds of audio received this session, up to the end of this window
    generation: int          # bumped by "clear"; stale windows are skipped
    voice_tail: np.ndarray   # audio just before this window, for the voice model's full frame


_STOP = object()


class StreamSession:
    def __init__(self, ws: WebSocket, session_id: str, analyzer: Analyzer, input_rate: int):
        s = analyzer.settings
        self.ws = ws
        self.session_id = session_id
        self.analyzer = analyzer
        self.settings = s
        self.input_rate = input_rate
        self._window_bytes = int(round(s.window_seconds * input_rate)) * 2
        self._pcm = bytearray()            # pending input not yet cut into a window
        self._voice_bytes = int(round(s.voice_update_seconds * input_rate)) * 2   # 0 = no live voice updates
        self._recent_bytes = int(round(analyzer.voice_samples / s.sample_rate * input_rate)) * 2
        self._recent = bytearray()         # the latest voice frame (4 s) of input, for the live updates
        self._since_voice = 0              # input bytes since the last live voice update
        self._voice_task: asyncio.Task | None = None
        self._samples_done = 0             # input-rate samples already cut into windows
        self._next_index = 0
        self.generation = 0
        self.history: deque[str] = deque(maxlen=s.context_windows - 1)
        self.language = analyzer.new_language_tracker()
        self.verdict = analyzer.new_verdict_tracker()
        self._voice_tail = np.zeros(0, np.float32)
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=s.max_backlog_windows)
        self.dropped = 0
        self.analysed = 0
        self.closed = False
        self._send_lock = asyncio.Lock()
        self.started_at = time.time()
        self.end_reason = "error"          # stop | disconnect | idle | error, once run() returns
        self.results: list[AnalysisResult] = []   # this call's results, for storage; cleared by "clear"

    # -- sending ------------------------------------------------------------------
    async def send(self, payload: dict) -> bool:
        if self.closed:
            return False
        text = json.dumps(payload, allow_nan=False, ensure_ascii=False)
        async with self._send_lock:
            try:
                await self.ws.send_text(text)
                return True
            except Exception:                       # the client went away
                self.closed = True
                return False

    async def send_error(self, code: str, message: str, fatal: bool = False) -> None:
        await self.send({"type": "error", "code": code, "message": message, "fatal": fatal})

    async def close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await self.ws.close(code=code, reason=reason[:120])
        except Exception:
            pass

    # -- lifecycle ----------------------------------------------------------------
    async def run(self) -> None:
        s = self.settings
        await self.send({
            "type": "ready",
            "session_id": self.session_id,
            "version": __version__,
            "sample_rate": s.sample_rate,
            "input_sample_rate": self.input_rate,
            "encoding": "pcm_s16le",
            "channels": 1,
            "window_seconds": s.window_seconds,
            "voice_update_seconds": s.voice_update_seconds,
            "models": {name: st["state"] for name, st in self.analyzer.model_status().items()},
        })
        worker = asyncio.create_task(self._worker(), name=f"voiceguard-worker-{self.session_id}")
        try:
            outcome = self.end_reason = await self._receive_loop()
            if self._voice_task is not None:
                self._voice_task.cancel()                 # no live update after the call ends
            if outcome == "stop":
                await self._flush_tail()
                await self.queue.put(_STOP)
                await worker                              # drain pending windows, then "stopped"
                await self.close(CLOSE_NORMAL, "stopped")
        finally:
            if not worker.done():
                worker.cancel()
                try:
                    await worker
                except (asyncio.CancelledError, Exception):
                    pass
            self._pcm.clear()
            self.history.clear()

    async def _receive_loop(self) -> str:
        s = self.settings
        while True:
            try:
                msg = await asyncio.wait_for(self.ws.receive(), timeout=s.idle_timeout_seconds)
            except asyncio.TimeoutError:
                await self.send_error(
                    "idle_timeout", f"No data for {s.idle_timeout_seconds:g} s; closing the session.", fatal=True
                )
                await self.close(CLOSE_IDLE, "idle timeout")
                return "idle"
            if msg["type"] == "websocket.disconnect":
                self.closed = True
                return "disconnect"
            data, text = msg.get("bytes"), msg.get("text")
            if data is not None:
                if len(data) > s.max_frame_bytes:
                    await self.send_error(
                        "frame_too_large", f"Binary frames must be at most {s.max_frame_bytes} bytes.", fatal=True
                    )
                    await self.close(CLOSE_TOO_BIG, "frame too large")
                    return "error"
                await self._on_audio(data)
            elif text is not None:
                if await self._on_control(text) == "stop":
                    return "stop"

    # -- audio --------------------------------------------------------------------
    async def _on_audio(self, data: bytes) -> None:
        self._pcm += data
        while len(self._pcm) >= self._window_bytes:
            audio = pcm16_to_float32(bytes(self._pcm[:self._window_bytes]))
            cut = find_cut(audio, self.input_rate, self.settings.min_window_seconds)   # end at a pause
            del self._pcm[:cut * 2]
            await self._enqueue(self._make_window(audio[:cut]))
        if self._voice_bytes:
            self._recent += data
            excess = len(self._recent) - self._recent_bytes
            if excess > 0:
                del self._recent[:excess - excess % 2]    # whole samples only, so PCM16 stays aligned
            self._since_voice += len(data)
            if self._since_voice >= self._voice_bytes:
                self._since_voice %= self._voice_bytes
                # One update in flight per session: a slow one is skipped, never queued behind.
                if self._voice_task is None or self._voice_task.done():
                    received = (self._samples_done + len(self._pcm) // 2) / self.input_rate
                    self._voice_task = asyncio.create_task(
                        self._voice_update(bytes(self._recent), received, self.generation)
                    )

    async def _voice_update(self, pcm: bytes, timestamp: float, generation: int) -> None:
        audio = pcm16_to_float32(pcm)
        if self.input_rate != self.settings.sample_rate:
            audio = resample(audio, self.input_rate, self.settings.sample_rate)
        try:
            judged = await run_in_threadpool(self.analyzer.voice_now, audio)
        except Exception:
            log.exception("Live voice update failed (session %s)", self.session_id)
            return
        if judged is None or generation != self.generation:  # CPU busy, or cleared meanwhile
            return
        score, status = judged
        await self.send({"type": "voice", "session_id": self.session_id, "timestamp": round(timestamp, 3),
                         "voice_score": score, "voice_status": status})

    def _make_window(self, audio: np.ndarray) -> _Window:
        """`audio` is float32 at the input rate; the window is resampled to the model rate."""
        self._samples_done += len(audio)
        if self.input_rate != self.settings.sample_rate:
            audio = resample(audio, self.input_rate, self.settings.sample_rate)
        window = _Window(
            audio, self._next_index, self._samples_done / self.input_rate, self.generation, self._voice_tail
        )
        self._voice_tail = self.analyzer.next_voice_tail(self._voice_tail, audio)
        self._next_index += 1
        return window

    async def _flush_tail(self) -> None:
        """On stop, analyse the partial last window if it is long enough to matter."""
        usable = len(self._pcm) - len(self._pcm) % 2
        if usable >= int(self.settings.min_flush_seconds * self.input_rate) * 2:
            window = self._make_window(pcm16_to_float32(bytes(self._pcm[:usable])))
            self._pcm.clear()
            await self.queue.put(window)
        self._pcm.clear()

    async def _enqueue(self, window: _Window) -> None:
        if self.queue.full():                           # analysis is behind real time:
            try:                                        # drop the oldest pending window
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            else:
                self.dropped += 1
                await self.send({
                    "type": "warning",
                    "code": "backlog_dropped",
                    "message": "Analysis is slower than real time; skipped an older audio window.",
                    "dropped_windows": self.dropped,
                })
        self.queue.put_nowait(window)

    # -- control messages ---------------------------------------------------------
    async def _on_control(self, text: str) -> str | None:
        try:
            msg = json.loads(text)
            kind = msg.get("type") if isinstance(msg, dict) else None
        except ValueError:
            kind = None
        if kind == "ping":
            await self.send({"type": "pong"})
        elif kind == "clear":
            self.generation += 1
            self._samples_done += len(self._pcm) // 2      # keep timestamps on the session clock
            self._pcm.clear()
            self._recent.clear()
            self._since_voice = 0
            self.history.clear()
            self.results.clear()
            self.verdict.reset()
            while not self.queue.empty():
                self.queue.get_nowait()
            await self.send({"type": "cleared"})
        elif kind == "stop":
            return "stop"
        else:
            await self.send_error(
                "bad_message", 'Text frames must be JSON: {"type": "ping" | "clear" | "stop"}. Send audio as binary.'
            )
        return None

    # -- analysis worker ----------------------------------------------------------
    async def _worker(self) -> None:
        stop = False
        while not stop:
            item = await self.queue.get()
            # Behind real time: analyse the pending windows as one. Speech-to-text costs about the
            # same for 8 s as for 4 s of audio, so this catches up instead of dropping windows.
            while item is not _STOP and not self.queue.empty():
                pending = self.queue.get_nowait()
                if pending is _STOP:
                    stop = True
                elif pending.generation != item.generation:
                    item = pending                          # the older one was cleared
                else:
                    item = _Window(np.concatenate([item.audio, pending.audio]), item.index,
                                   pending.timestamp, item.generation, item.voice_tail)
            if item is _STOP:
                break
            if item.generation != self.generation:
                continue
            try:
                result, text = await run_in_threadpool(
                    self.analyzer.analyze,
                    item.audio, list(self.history), self.session_id, item.index, item.timestamp, self.language,
                    item.voice_tail,
                )
            except Exception:
                log.exception("Analysis failed (session %s, window %d)", self.session_id, item.index)
                await self.send_error("analysis_failed", "Could not analyse one audio window; continuing.")
                continue
            if item.generation != self.generation:          # cleared while this window was analysed
                continue
            self.history.append(text)
            self.analysed += 1
            self.verdict.update(result)
            result.verdict = self.verdict.snapshot()
            self.results.append(result)
            await self.send(result.model_dump(mode="json"))
        await self.send({
            "type": "stopped",
            "windows_analysed": self.analysed,
            "dropped_windows": self.dropped,
            "verdict": self.verdict.snapshot(final=True).model_dump(mode="json"),
        })


def parse_sample_rate(raw: str | None, lo: int, hi: int, default: int) -> int | None:
    """The client's declared input rate, or None if invalid."""
    if raw is None or raw == "":
        return default
    try:
        value = int(float(raw))
    except (ValueError, OverflowError):              # "abc", "nan", "inf"
        return None
    return value if lo <= value <= hi else None
