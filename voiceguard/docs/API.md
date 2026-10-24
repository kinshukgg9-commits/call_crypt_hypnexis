# VoiceGuard API contract

This is the single reference for the frontend and backend. The `VoiceGuardClient` class in
`frontend/index.html` (the CallCrypt page) implements the client side of it; copy it or mirror it.
REST endpoints are also described live at `/docs` (OpenAPI at `/openapi.json`).

> Results are automated risk indicators, not proof. Scores are not calibrated probabilities.

## The verdict: one result per call

Everything the result page needs is in one object. It arrives with every live result
(the running verdict), in the `stopped` message (the final verdict), and in the upload
response.

```ts
interface Verdict {
  verdict: "safe" | "ai_voice" | "scam" | "inconclusive";
  title: string;        // headline, ready to display
  message: string;      // one or two sentences of guidance, ready to display
  final: boolean;       // false while the call is still going
  scam_score: number;   // 0..1, strongest scam evidence during the call
  reasons: string[];    // scam tactics noticed, strongest first
  ai_voice: boolean;    // voice looks AI-generated (can also be true when verdict is "scam")
  voice_score: number | null;  // 0..1: share of assessed windows that look synthetic; null if none assessed
  voice_flagged: number;       // assessed windows that look synthetic
  voice_windows: number;       // windows with enough speech to assess the voice
  windows: number;
  speech_seconds: number;
}
```

| `verdict` | Final title | Meaning |
|---|---|---|
| `safe` | Not a scam call | Nothing said matches scam tactics, and the voice was not found to be AI-generated. |
| `ai_voice` | AI-generated voice, likely not a scam | The voice looks synthetic, but nothing said matches scam tactics. |
| `scam` | Scam call detected | What was said matches scam tactics. `ai_voice` says whether the voice is also synthetic. |
| `inconclusive` | Not enough to judge | Too little clear speech, or speech-to-text was unavailable. Never shown as "safe". |

While `final` is false the titles are provisional ("No scam detected so far", "AI-generated
voice, no scam detected so far", "Listening…"); a call in progress is never declared safe.
A `safe` or `ai_voice` verdict can still list `reasons`: weak cues that were heard but do not
add up to a scam (the message says so).
A `scam` verdict is sticky: once reached, it stays for the rest of the call.

Rendering: use `verdict` for the colour/icon, and print `title` and `message` as they are.

`ai_voice` is true when at least a third of the assessed windows look synthetic (and at
least two of them, unless only one was assessed), or when any one window scores
`thresholds.voice_strong` or more; `/api/config` reports the thresholds as
`thresholds.voice`, `thresholds.voice_share` and `thresholds.voice_strong`. A window's
`voice_flag: "elevated"` is only a hint: label it as AI-like only when the verdict's
`ai_voice` is true, or the page will contradict its own result. The voice check needs the caller's sound
unchanged: an uploaded recording, or live audio captured from a browser tab. Through a
loudspeaker and a microphone, room echo and noise hide what marks a voice as synthetic, so
`ai_voice: false` on a microphone session does not mean the voice is human. Say so in your UI.

Reason names: `Known scam script`, `Sensitive info request`, `Threat or confinement`,
`Remote access request`, `Money movement`, `Secrecy`, `Prize or refund bait`,
`Account or KYC threat`, `Authority impersonation`, `Urgency`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET/HEAD | `/health` | Liveness probe. Always `{"ok": true}` while the process is up. |
| GET | `/api/status` | Model readiness and load (`models.*.state`: `not_loaded`, `loading`, `ready`, `error`, `disabled`). |
| GET | `/api/config` | Audio format, thresholds, limits and all user-facing copy. |
| WS | `/ws/{session_id}?sample_rate=16000` | Live analysis stream. |
| POST | `/analyze-file` | Analyse an uploaded recording (multipart field `file`). |
| GET | `/` | The bundled UI (`frontend/`), when the backend serves it. |

Models load in the background after startup, so `/health` answers immediately. Show
`/api/status` so users know when a model is still loading.

## Live stream (WebSocket)

### Connect

```
wss://HOST/ws/{session_id}?sample_rate=16000
```

- `session_id`: 1–64 chars of `A-Z a-z 0-9 - _` (`crypto.randomUUID()` works). One live connection per id.
- `sample_rate`: the rate of the PCM you will send, 8000–192000. Default 16000. Send the
  `AudioContext`'s real `sampleRate`; the server resamples to 16 kHz itself.

### Client → server

| Frame | Content |
|---|---|
| binary | Raw PCM, signed 16-bit little-endian, mono, at `sample_rate`. Any frame size up to 1 MiB; ~128 ms per frame is ideal. |
| text | `{"type": "ping"}` keepalive (every ~15 s; some proxies close quiet sockets) |
| text | `{"type": "clear"}` forget the call so far (transcript context and verdict) |
| text | `{"type": "stop"}` analyse what is buffered, send `stopped` with the final verdict, then close with 1000 |

Closing the socket without `stop` is also fine (buffers are dropped), but you get no final verdict.

### Server → client

Every message is JSON with a `type`. Ignore types you don't know.

```ts
interface Ready {
  type: "ready";
  session_id: string;
  version: string;
  sample_rate: 16000;
  input_sample_rate: number;
  encoding: "pcm_s16le";
  channels: 1;
  window_seconds: number;        // longest window (4)
  voice_update_seconds: number;  // how often "voice" messages come (0.5; 0 = off)
  models: { transcription: ModelState; anti_spoof: ModelState };
}

interface Result {               // one per analysed window
  type: "result";
  session_id: string;
  timestamp: number;             // seconds of audio received this session, at the end of this window
  window_index: number;          // 0-based, increasing (gaps: windows dropped, or analysed together)
  duration: number;              // 3-4 s: windows end at a pause so words aren't split; longer when
                                 // the server fell behind and analysed pending windows together
  speech_seconds: number | null; // speech detected in this window
  transcript: string;            // text heard in this window ("" if none)
  transcript_status: "ok" | "no_speech" | "unavailable";
  language: string | null;       // e.g. "en", "hi"
  scam_score: number;            // this window's recent context (last ~24 s)
  risk: "low" | "medium" | "high";
  reasons: string[];
  voice_score: number | null;    // this window's synthetic-speech score; null unless voice_status is "ok"
  voice_status: "ok" | "insufficient_audio" | "clipped" | "unavailable";
  voice_flag: "normal" | "elevated" | "insufficient_audio" | "clipped" | "unavailable";
  processing_ms: number;
  verdict: Verdict;              // the running call-level verdict (final: false)
}

interface Voice {                // every VOICE_UPDATE_SECONDS (0.5 s) of audio received; off when 0
  type: "voice";
  session_id: string;
  timestamp: number;             // seconds of audio received this session
  voice_score: number | null;    // synthetic-speech score of the latest 4 s, judged like a window's
  voice_status: "ok" | "insufficient_audio" | "clipped" | "unavailable";
}                                // skipped, not queued, when the server is busy; the verdict still
                                 // comes only from the windows (Result.verdict)

interface Stopped { type: "stopped"; windows_analysed: number; dropped_windows: number; verdict: Verdict } // final: true
interface Pong    { type: "pong" }
interface Cleared { type: "cleared" }
interface Warning { type: "warning"; code: "backlog_dropped"; message: string; dropped_windows: number }
interface ErrorMsg { type: "error"; code: string; message: string; fatal: boolean }
type ModelState = "disabled" | "not_loaded" | "loading" | "ready" | "error";
```

The per-window fields (`risk`, `voice_flag`, …) are detail for a live transcript view. For
the result itself, use `verdict`.

### Where the audio comes from

The server only sees PCM, so the page decides what to capture:

- **Microphone** (`getUserMedia`): a phone call on speaker. Turn off `echoCancellation`,
  `noiseSuppression` and `autoGainControl`. Good for what is said; weak for the AI-voice check.
- **A browser tab's audio** (`getDisplayMedia({ video: true, audio: true })`, then stop the
  video track): a call taken on the computer. The sound arrives unchanged, so the AI-voice
  check works. Chrome and Edge on a computer only; the user must tick "Also share tab audio".

`VoiceGuardClient` (in `frontend/index.html`) does both: `vg.start()` or `vg.start({ source: "tab" })`.
`VoiceGuardClient.canShareAudio()` tells you whether to offer the second one, and the
`onEnded` callback fires when the user presses the browser's "Stop sharing". `onLevel` reports
the loudness (0 to 1) of each audio frame, for a level meter.

### Errors and close codes

`fatal: true` errors are followed by the server closing the socket.

| code | fatal | close | when |
|---|---|---|---|
| `invalid_session_id` | yes | 4400 | bad `session_id` |
| `invalid_sample_rate` | yes | 4400 | `sample_rate` is not a number or is out of range |
| `session_active` | yes | 4409 | that `session_id` is already streaming |
| `server_busy` | yes | 4429 | `MAX_SESSIONS` reached; retry later |
| `idle_timeout` | yes | 4408 | nothing received for `SESSION_IDLE_TIMEOUT` s |
| `frame_too_large` | yes | 1009 | binary frame over `MAX_FRAME_BYTES` |
| `internal_error` | yes | 1011 | unexpected server failure |
| `bad_message` | no | – | text frame that isn't a known control message |
| `analysis_failed` | no | – | one window failed; the session continues |

## Upload: `POST /analyze-file`

Multipart form, field `file` (WAV, MP3, M4A, OGG, FLAC, WEBM …). Max `MAX_UPLOAD_MB` (25)
and `MAX_UPLOAD_SECONDS` (600).

```ts
interface UploadAnalysis {
  session_id: string;            // "upload-xxxxxxxx"
  filename: string | null;
  duration: number;              // seconds
  window_seconds: number;
  verdict: Verdict;              // the result for the whole recording (final: true)
  results: Result[];             // per-window detail, same shape as live results
  summary: { windows: number; risk: string; max_scam_score: number; reasons: string[];
             max_voice_score: number | null; voice_flag: string };
}
```

Minimal HTML/JS example:

```js
const form = new FormData();
form.append("file", fileInput.files[0]);
const res = await fetch(API + "/analyze-file", { method: "POST", body: form });
const { verdict } = await res.json();
card.className = "verdict verdict-" + verdict.verdict;   // safe | ai_voice | scam | inconclusive
title.textContent = verdict.title;
message.textContent = verdict.message;
```

## HTTP errors

Every HTTP error body is `{"detail": string, "code": string}`.

| status | code | when |
|---|---|---|
| 400 | `empty_file` | zero-byte upload |
| 413 | `file_too_large` / `audio_too_long` | over `MAX_UPLOAD_MB` / `MAX_UPLOAD_SECONDS` |
| 415 | `unsupported_audio` | not decodable as audio |
| 422 | `no_audio` / `invalid_request` | no audio stream / missing `file` field |
| 404 | `http_404` | unknown path |

## Frontend on another origin (Vercel)

- `frontend/config.js`: set `window.VOICEGUARD_API = "https://your-backend"`. The client
  derives the `wss://` address from it.
- CORS: the API allows every origin by default (it uses no cookies or auth). To restrict it,
  set `CORS_ORIGINS` on the backend, e.g. `https://your-app.vercel.app` or
  `https://*.vercel.app` (comma-separated; `none` disables cross-origin use).
- The backend must be served over HTTPS, or browsers will block it from an HTTPS page.
