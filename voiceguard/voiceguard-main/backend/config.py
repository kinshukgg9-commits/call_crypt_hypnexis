"""Runtime settings, read once from environment variables.

Every setting has a safe default, so `uvicorn backend.app:app` works with no
configuration. Invalid values fail fast at startup with a message naming the
variable, instead of failing later inside a request.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ANTISPOOF_BACKENDS = ("onnx", "aasist", "torchscript", "none")
DEFAULT_VOICE_MODEL = "ml/models/antideepfake-w2v-small.onnx"


def _raw(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _str(name: str, default: str | None) -> str | None:
    value = _raw(name)
    return default if value is None else value


def _int(name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    _check_range(name, value, lo, hi)
    return value


def _float(name: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None
    _check_range(name, value, lo, hi)
    return value


def _bool(name: str, default: bool) -> bool:
    raw = _raw(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false, got {raw!r}")


def _check_range(name: str, value: float, lo: float | None, hi: float | None) -> None:
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError(f"{name} must be between {lo} and {hi}, got {value}")


def _path(name: str, default: str) -> Path:
    path = Path(_str(name, default) or default)
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Settings:
    # Audio contract. The models expect 16 kHz mono; clients may stream at another
    # rate (declared with ?sample_rate=) and the server resamples each window.
    sample_rate: int = 16000
    window_seconds: float = 4.0       # longest window
    min_window_seconds: float = 3.0   # a window may end early at a pause, so words aren't split
    context_windows: int = 6          # windows of transcript scored together (~24 s)
    min_flush_seconds: float = 0.5    # shortest trailing chunk still worth analysing

    # Streaming limits
    max_sessions: int = 8
    max_backlog_windows: int = 2      # beyond this, the oldest pending window is dropped
    idle_timeout_seconds: float = 60.0
    max_frame_bytes: int = 1 << 20
    min_input_sample_rate: int = 8000
    max_input_sample_rate: int = 192000

    # Upload limits
    max_upload_mb: float = 25.0
    max_upload_seconds: float = 600.0

    # Shared CPU: analyses run one at a time by default (models are CPU-bound).
    max_concurrent_analyses: int = 1

    # Speech-to-text (faster-whisper)
    transcription_enabled: bool = True
    whisper_model: str = "small"
    whisper_lang: str | None = None   # None = auto-detect (sticky per session, see below)
    language_recheck_windows: int = 4 # reuse a detected language for N windows, then re-detect
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 5        # beam search: ~10% slower than greedy, more accurate
    whisper_cpu_threads: int = 0      # 0 = library default
    whisper_download_root: str | None = None

    # Speech detection (Silero VAD): silent windows skip speech-to-text, and the voice is
    # only judged on windows that actually contain speech.
    vad_enabled: bool = True
    min_transcribe_speech_seconds: float = 0.25
    min_voice_speech_seconds: float = 1.5

    # Voice authenticity (anti-spoofing); see ml/anti_spoof.py for the bundled models
    antispoof_backend: str = "onnx"   # onnx needs no PyTorch; aasist/torchscript do
    antispoof_onnx: Path = field(default_factory=lambda: ROOT / DEFAULT_VOICE_MODEL)
    antispoof_ckpt: Path = field(default_factory=lambda: ROOT / "ml/weights/AASIST.pth")
    antispoof_model_py: Path = field(default_factory=lambda: ROOT / "ml/weights/AASIST.py")
    antispoof_config: Path = field(default_factory=lambda: ROOT / "ml/weights/AASIST.conf")
    antispoof_spoof_index: int = 0    # both bundled models: index 0 = spoof, 1 = bona fide
    antispoof_input_samples: int = 0  # audio the model sees per window; 0 = 4 s
    torch_num_threads: int = 0        # 0 = min(4, CPU count): faster than torch's default here

    # Call-level verdict (see backend/verdict.py)
    verdict_scam_threshold: float = 0.60
    evidence_seconds: float = 120.0       # how long a scam tactic stays "active" after it is heard
    verdict_min_voice_windows: int = 2    # voice windows needed before a live "AI voice" verdict
    verdict_min_speech_seconds: float = 2.0
    voice_threshold: float = 0.50         # a window "looks synthetic" at or above this score
    voice_min_share: float = 0.33         # share of such windows that makes the voice "AI-generated"
    voice_strong: float = 0.95            # one window at or above this is enough on its own; above 1 = off

    # Per-window risk labels
    risk_high: float = 0.70
    risk_medium: float = 0.40

    # Serving
    preload_models: bool = True
    frontend_dir: Path = field(default_factory=lambda: ROOT / "frontend")
    cors_origins: tuple[str, ...] = ("*",)   # no cookies or auth, so any origin may call the API
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.antispoof_backend not in ANTISPOOF_BACKENDS:
            raise ValueError(
                f"ANTISPOOF_BACKEND must be one of {', '.join(ANTISPOOF_BACKENDS)}, "
                f"got {self.antispoof_backend!r}"
            )
        if not 0 <= self.risk_medium < self.risk_high <= 1:
            raise ValueError("Thresholds must satisfy 0 <= RISK_MEDIUM < RISK_HIGH <= 1")
        if self.context_windows < 1:
            raise ValueError("CONTEXT_WINDOWS must be at least 1")
        if self.window_seconds <= 0:
            raise ValueError("WINDOW_SECONDS must be positive")
        if not 0 < self.min_window_seconds <= self.window_seconds:
            raise ValueError("MIN_WINDOW_SECONDS must be between 0 and WINDOW_SECONDS")

    @property
    def window_samples(self) -> int:
        return int(round(self.sample_rate * self.window_seconds))

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @classmethod
    def from_env(cls) -> Settings:
        cors = _str("CORS_ORIGINS", "*") or "*"
        if cors.lower() == "none":
            cors = ""
        window_seconds = _float("WINDOW_SECONDS", 4.0, 1.0, 30.0)
        return cls(
            window_seconds=window_seconds,
            min_window_seconds=min(_float("MIN_WINDOW_SECONDS", 3.0, 0.5, 30.0), window_seconds),
            vad_enabled=_bool("VAD_ENABLED", True),
            min_voice_speech_seconds=_float("MIN_VOICE_SPEECH_SECONDS", 1.5, 0.0, 30.0),
            verdict_scam_threshold=_float("VERDICT_SCAM_THRESHOLD", 0.60, 0.0, 1.0),
            evidence_seconds=_float("EVIDENCE_SECONDS", 120.0, 1.0, 86400.0),
            verdict_min_voice_windows=_int("VERDICT_MIN_VOICE_WINDOWS", 2, 1, 100),
            verdict_min_speech_seconds=_float("VERDICT_MIN_SPEECH_SECONDS", 2.0, 0.0, 600.0),
            context_windows=_int("CONTEXT_WINDOWS", 6, 1, 50),
            max_sessions=_int("MAX_SESSIONS", 8, 1, 1000),
            max_backlog_windows=_int("MAX_BACKLOG_WINDOWS", 2, 1, 100),
            idle_timeout_seconds=_float("SESSION_IDLE_TIMEOUT", 60.0, 5.0, 3600.0),
            max_frame_bytes=_int("MAX_FRAME_BYTES", 1 << 20, 1024, 64 << 20),
            max_upload_mb=_float("MAX_UPLOAD_MB", 25.0, 0.01, 2048.0),
            max_upload_seconds=_float("MAX_UPLOAD_SECONDS", 600.0, 1.0, 86400.0),
            max_concurrent_analyses=_int("MAX_CONCURRENT_ANALYSES", 1, 1, 64),
            transcription_enabled=_bool("TRANSCRIPTION_ENABLED", True),
            whisper_model=_str("WHISPER_MODEL", "small") or "small",
            whisper_lang=_str("WHISPER_LANG", None),
            language_recheck_windows=_int("LANGUAGE_RECHECK_WINDOWS", 4, 0, 1000),
            whisper_device=_str("WHISPER_DEVICE", "cpu") or "cpu",
            whisper_compute_type=_str("WHISPER_COMPUTE_TYPE", "int8") or "int8",
            whisper_beam_size=_int("WHISPER_BEAM_SIZE", 5, 1, 10),
            whisper_cpu_threads=_int("WHISPER_CPU_THREADS", 0, 0, 256),
            whisper_download_root=_str("WHISPER_DOWNLOAD_ROOT", None),
            antispoof_backend=(_str("ANTISPOOF_BACKEND", "onnx") or "onnx").lower(),
            antispoof_onnx=_path("ANTISPOOF_ONNX", DEFAULT_VOICE_MODEL),
            antispoof_ckpt=_path("ANTISPOOF_CKPT", "ml/weights/AASIST.pth"),
            antispoof_model_py=_path("ANTISPOOF_MODEL_PY", "ml/weights/AASIST.py"),
            antispoof_config=_path("ANTISPOOF_CONFIG", "ml/weights/AASIST.conf"),
            antispoof_spoof_index=_int("ANTISPOOF_SPOOF_INDEX", 0, 0, 1),
            antispoof_input_samples=_int("ANTISPOOF_INPUT_SAMPLES", 0, 0, 16000 * 60),
            torch_num_threads=_int("TORCH_NUM_THREADS", 0, 0, 256),
            risk_high=_float("RISK_HIGH", 0.70, 0.0, 1.0),
            risk_medium=_float("RISK_MEDIUM", 0.40, 0.0, 1.0),
            voice_threshold=_float("VOICE_THRESHOLD", 0.50, 0.0, 1.0),
            voice_min_share=_float("VOICE_MIN_SHARE", 0.33, 0.0, 1.0),
            voice_strong=_float("VOICE_STRONG", 0.95, 0.0, 2.0),
            preload_models=_bool("PRELOAD_MODELS", True),
            frontend_dir=_path("FRONTEND_DIR", "frontend"),
            cors_origins=tuple(o.strip().rstrip("/") for o in cors.split(",") if o.strip()),
            log_level=(_str("LOG_LEVEL", "INFO") or "INFO").upper(),
        )
