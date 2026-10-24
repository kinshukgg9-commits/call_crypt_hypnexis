"""Thread-safe lazy loading for heavy models, with a visible state for /api/status."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
log = logging.getLogger("voiceguard.models")


class LazyModel(Generic[T]):
    """Loads a model on first use (or on preload) exactly once.

    A failed load is remembered so every request doesn't pay for it again, and is
    retried after `retry_after` seconds (e.g. a model download that hit a network blip).
    """

    def __init__(self, name: str, loader: Callable[[], T] | None, detail: str = "", retry_after: float = 60.0):
        self.name = name
        self.detail = detail
        self._loader = loader
        self._retry_after = retry_after
        self._lock = threading.Lock()
        self._value: T | None = None
        self._failed_at = 0.0
        self.error: str | None = None
        self.state = "disabled" if loader is None else "not_loaded"

    def get(self) -> T | None:
        """The loaded model, or None if it is disabled or failed to load."""
        if self.state == "ready":
            return self._value
        if self.state == "disabled":
            return None
        if self.state == "error" and time.monotonic() - self._failed_at < self._retry_after:
            return None
        with self._lock:
            if self.state in ("not_loaded", "error"):
                self._load()
        return self._value if self.state == "ready" else None

    def _load(self) -> None:
        assert self._loader is not None
        self.state = "loading"
        started = time.perf_counter()
        log.info("Loading %s (%s)", self.name, self.detail)
        try:
            self._value = self._loader()
        except Exception as exc:  # any failure leaves the service up, with this model unavailable
            self.error = f"{type(exc).__name__}: {exc}"
            self._failed_at = time.monotonic()
            self.state = "error"
            log.error("Could not load %s: %s", self.name, self.error)
        else:
            self.error = None
            self.state = "ready"
            log.info("Loaded %s in %.1f s", self.name, time.perf_counter() - started)

    def status(self) -> dict:
        return {"state": self.state, "detail": self.detail, "error": self.error}
