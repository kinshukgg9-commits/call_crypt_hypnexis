# VoiceGuard

Listens to a phone call (live, or a recording) and gives one of three results:

| Result | When |
|---|---|
| **Not a scam call** | Nothing said matches scam tactics, and no signs of an AI voice were found. |
| **AI-generated voice, likely not a scam** | The voice looks synthetic, but nothing said matches scam tactics. |
| **Scam call detected** | What was said matches scam tactics (the result also says if the voice is AI-generated). |

If there is too little clear speech to tell, it says "Not enough to judge" instead of guessing.

> Results are automated risk indicators, not proof.

```
Browser (plain HTML/JS)                    Backend (one FastAPI process, Python 3.11)
mic → AudioWorklet → PCM16 ──WebSocket──►  per-session buffer (RAM only), cut into 3-4 s
                                           windows that end at a pause:
                                            1. speech detection (silence and noise are skipped)
                                            2a. faster-whisper → what was said → scam rules
                                            2b. voice model → does the voice look AI-generated?
                                            3. verdict tracker → one result for the whole call
     ◄──────── JSON result + verdict ──────┘
```

The frontend/backend contract is in [docs/API.md](docs/API.md).

## Run locally

No API keys, accounts or database are needed. You need Python 3.11.

**Windows, the short way:** double-click `run.bat`. The first run sets everything up (a few
minutes, needs internet); after that it starts the server and opens the page by itself.

**Windows (PowerShell)**, from the project folder:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.app:app --port 8000 --workers 1
```

In a second PowerShell window, check that it is up:

```powershell
Invoke-RestMethod http://localhost:8000/health      # ok: True
Invoke-RestMethod http://localhost:8000/api/status  # models: ready / loading / error
```

If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

**macOS / Linux:**

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app:app --port 8000 --workers 1
```

Open http://localhost:8000 in Chrome (the mic works on localhost without HTTPS).
API docs: http://localhost:8000/docs. `requirements.txt` is the server only;
`requirements-dev.txt` adds the tests and the tools for rebuilding the voice model.

**Docker** (optional; Docker Desktop must be running):

```powershell
docker build -t voiceguard .
docker run --rm -p 8000:8000 voiceguard
```

### What works before the models are there

- **Voice model:** ships with the project (`ml/models/antideepfake-w2v-small.onnx` and its
  three `.w*` weight files, 120 MB together, fine-tuned for this project), so the voice check
  works offline from the first
  start. If those files are missing or can't be loaded, every result reports
  `voice_status: "unavailable"` with `voice_score: null`; the scam check keeps working and
  the result says "Voice authenticity could not be checked."
- **Speech model (Whisper):** downloaded once on the first start (`small` is ~460 MB), so the
  first start needs internet. Until it has loaded, or if the download fails, the server still
  runs: `/health`, `/api/status`, `/api/config` and the UI all work, results report
  `transcript_status: "unavailable"`, and the verdict is "Not enough to judge". It never says
  "Not a scam call" about speech it could not read. A failed load is retried every minute.
- **Tests:** `pytest` needs no model downloads at all.

## Deploy: frontend on Vercel, backend on a container host

The frontend is static HTML/JS with no build step, so Vercel can host it as it is. The
backend can't run on Vercel (it needs a long-running process for WebSockets and a few
hundred MB of memory for the models), so it goes on a container host and the two are joined
by one line.

1. **Backend.** Two images are provided:
   - `Dockerfile.free` for free hosts with 512 MB of memory (Render's free plan: push the repo
     to GitHub, then in Render choose New > Blueprint and pick it; `render.yaml` does the rest).
     It uses the smallest speech model and peaked at about 425 MB under the heaviest test.
   - `Dockerfile` for hosts with 1 GB or more: the `small` speech model, more accurate.

   Health check `/health`, one instance. Free hosts sleep when idle and take about a minute
   to wake. For a quick demo you can instead run the backend on a laptop and expose it:
   `cloudflared tunnel --url http://localhost:8000`.

   The voice model is in the repository as three weight files under 50 MB each, so a plain
   `git push` to GitHub works (no Git LFS).
2. **Point the frontend at it.** In [frontend/config.js](frontend/config.js):
   ```js
   window.VOICEGUARD_API = "https://your-backend.example.com";
   ```
3. **Frontend.** Push the repo to GitHub, import it in Vercel, and set **Root Directory** to
   `frontend` (framework preset "Other", no build command). Or from a terminal:
   `cd frontend && npx vercel --prod`.

Nothing else is needed: the API accepts requests from any origin by default. To lock it to
your site, set `CORS_ORIGINS=https://your-app.vercel.app` on the backend
(`https://*.vercel.app` also covers preview deployments).

Your own HTML page can use the backend in a few lines; see the example in
[docs/API.md](docs/API.md), or import `frontend/voiceguard-client.js`.

**One file to pass around:** `python scripts/build_single_html.py` packs the whole frontend
into `voiceguard-frontend.html`. Opened with a double-click it talks to a backend on
`http://localhost:8000`; add `--api https://your-backend.example.com` to bake in another one.

## Settings

Environment variables; see [.env.example](.env.example) for all of them
(`uvicorn backend.app:app --env-file .env`).

| Variable | Default | Notes |
|---|---|---|
| `WHISPER_MODEL` | `small` | `base` / `tiny` for small machines |
| `WHISPER_LANG` | auto | `hi` or `en` to force a language (faster) |
| `VERDICT_SCAM_THRESHOLD` | `0.60` | scam evidence needed for "Scam call detected" |
| `VOICE_THRESHOLD` | `0.50` | a speech window "looks synthetic" at or above this score |
| `VOICE_MIN_SHARE` | `0.33` | share of such windows needed for "AI-generated"; raise it for fewer false alarms |
| `VOICE_STRONG` | `0.95` | one window at or above this is enough on its own; `1.01` turns that off, `0.50` makes any flagged window count |
| `ANTISPOOF_ONNX` | `ml/models/antideepfake-w2v-small.onnx` | the voice model; `ml/models/aasist.onnx` is the MIT-licensed alternative |
| `ANTISPOOF_BACKEND` | `onnx` | `aasist` / `torchscript` run on PyTorch instead; `none` disables it |
| `CORS_ORIGINS` | `*` | restrict to your frontend's origin in production |
| `MAX_SESSIONS` | `8` | concurrent live sessions |
| `DATABASE_URL` | unset | `mysql://user:pass@host:3306/voiceguard?ssl=true` turns on call history and feedback storage ([docs/STORAGE.md](docs/STORAGE.md)) |

## Tests

```bash
pip install -r requirements-dev.txt           # once
pytest                                        # fast, offline (fake models)
VOICEGUARD_MODEL_TESTS=1 pytest -m models     # also load the real models
                                              # PowerShell: $env:VOICEGUARD_MODEL_TESTS = "1"; pytest -m models
python scripts/eval_rules.py                  # precision/recall of the scam rules
python -m ml.evaluate --genuine DIR --spoof DIR   # the voice check on your own real / AI clips
VOICEGUARD_TEST_DATABASE_URL=mysql://... pytest tests/test_storage.py   # the MySQL storage (throw-away database)
```

## Memory

Measured for the whole server (one thread, Windows; Linux will differ a little):

| `WHISPER_MODEL` | Peak memory | Time per window on one fast CPU thread |
|---|---|---|
| `tiny` | ~400 MB (one call); ~425 MB with two live sessions plus uploads | ~0.8 s |
| `base` | ~470 MB | ~1.5 s |
| `small` | ~750 MB | ~4 s |

About 140 MB of that is the voice model, which runs on onnxruntime with 8-bit weights (no
PyTorch). Smaller speech models make more transcription mistakes, especially in Hindi, so
scam phrases can be missed. Free hosts also give only a fraction of a CPU: expect uploads to be slow there, and
live mode to skip windows when it can't keep up.

## How the result is decided

- **Scam evidence adds up over the call.** Each tactic heard (see `backend/scam_rules.py`)
  has a weight and stays active for two minutes; active tactics combine, and the call is
  judged on its strongest moment. One everyday phrase ("right now", "pay the amount", "the
  police") never convicts on its own; a request for an OTP/PIN/CVV or a scam-only phrase
  ("digital arrest", "safe account") does. Once a call reaches the scam result it stays there.
- **The voice is judged on speech only, window by window.** Windows without enough speech
  are skipped, and each scored window gets a full 4 s of continuous audio. Real voices score
  near zero almost every time, but many AI voices are caught only in some windows. So the
  voice counts as AI-generated when at least a third of the speech windows look synthetic
  (and at least two of them), not on an average. One mildly odd window doesn't flip it;
  one window the model is nearly certain about (0.95 or more) does. A flagged window
  that falls short of this is shown as "Voice flagged" and the result says so in words,
  so the page never shows "AI voice" next to "Not a scam call".
- Details and thresholds: `backend/verdict.py`.

## The AI-voice check: what to expect

The voice model is the public AntiDeepfake detector, fine-tuned for this project on 20.5 hours
of speech: 13.4 h of AI voices (Microsoft's neural voices including the "Multilingual" ones,
Google Translate, Kokoro, Piper, ElevenLabs, OpenAI, Gemini and IndicTTS voices; English, Hindi,
Hinglish and others) and 7.1 h of real people (Hindi and English read on phones, Indian-English
lectures, Hinglish, audiobooks, phone-banking calls in ten languages). Every clip was heard as
a clean file, over a phone line, through a loudspeaker in an echoing room into a microphone,
in background noise, and through real codecs (MP3, AAC, Opus, AMR, G.711). The fine-tuned
weights are then averaged with the original ones, which keeps the original's reach on voice
generators we had no samples of. See `ml/train/`.

Measured on 255 AI-voice calls and 120 real-voice calls of 20-30 s each, with the server's
own windowing and rule. 200 of the AI calls and 70 of the real ones come from voices,
generators (Cartesia, CosyVoice 2, Orpheus) and recording sources the training never saw:

| Audio | AI calls caught | before fine-tuning | Real calls wrongly flagged | before |
|---|---|---|---|---|
| File as generated / recorded | 250 of 255 | 244 | 2 of 119 | 4 |
| Narrow-band phone line (simulated) | 239 of 255 | 223 | 3 of 120 | 3 |
| Loudspeaker to microphone (simulated room) | 168 of 255 | 99 | 4 of 120 | 5 |

On the never-seen part alone: 195 of 200, 185 of 200 and 118 of 200 caught, with 1 of 70,
1 of 71 and 0 of 71 real calls wrongly flagged.

What this means in practice:

- **Uploaded recordings and shared tab audio** are checked well, also over a phone-quality line.
- **A voice played through a loudspeaker into the microphone** is now caught for the voice
  families the model was trained on (Microsoft and Kokoro voices it had never heard: 96 of 96
  in the simulated room). For generators it has no samples of it is still weak there (18 of
  96), so "no signs of an AI voice" on a microphone session does not prove the caller is human.
- **Studio-read speech** (voice-over style recordings with the silences gated out) is still
  often flagged: 34 of 65 such calls, down from 53. Ordinary calls and recordings are not.
- New voice generators appear all the time; expect some to get through until samples of them
  are added to the training.

The microphone rows are simulations (a different simulator from the one used in training),
not recordings in real rooms. Run `python -m ml.evaluate` on the kind of audio you will demo
with.

### Training it again

    pip install torch numpy scipy av soundfile onnx onnxruntime     # a CUDA build of torch
    python -m ml.train.train prep      # clips in data/voice/raw/real/<source>/ and raw/fake/<source>/
    python -m ml.train.train fit       # about 25 minutes on an 8 GB GPU
    python -m ml.train.train blend     # average with the public weights
    python scripts/build_voice_model.py --weights data/voice/run/blend50.pt

`fit` prints how the model does on voices and sources it is not trained on (the `TEST_*` lists
in `ml/train/train.py`). The shipped model is the 50/50 average of `run/detector.pt` and the
public checkpoint. The exported model has the same size and memory use as before.

## Performance

On a 24-thread laptop CPU with `WHISPER_MODEL=small`: about 1.1–1.7 s of processing per 3–4 s
window, so live mode keeps up with real time; silent windows take ~5 ms. The first window of
a session (and every fifth after it) takes ~3 s because Whisper also detects the language,
which is then reused; set `WHISPER_LANG` to skip detection. On a slower machine the server drops the oldest pending
window and warns rather than letting results lag; use `WHISPER_MODEL=base` there.

## Layout

```
backend/   app.py (routes) · session.py (WebSocket) · pipeline.py (per-window analysis) · verdict.py (the result)
           scam_rules.py · risk_engine.py · transcription.py · audio_io.py · config.py · schemas.py · messages.py
ml/        vad.py · audio_preprocess.py · anti_spoof.py · evaluate.py · lazy.py
           train/ (fine-tuning the voice model: train.py · augment.py · audio.py)
           models/antideepfake-w2v-small.onnx + .w0 .w1 .w2 (the voice model) · models/aasist.onnx (alternative)
frontend/  index.html · styles.css · app.js (the page) · voiceguard-client.js · pcm-worklet.js (talking to the server)
           demo.js (the scripted demo) · config.js (backend address) · vercel.json
scripts/   eval_rules.py · make_demo.py (regenerates frontend/demo.js) · build_single_html.py (one-file frontend)
           build_voice_model.py (exports the voice model to ONNX)
           fetch_aasist.py + export_aasist_onnx.py
run.bat    double-click start for Windows
tests/     protocol, API, verdict, rules, units, opt-in real-model tests
```

## Limits to know about

- The scam rules are patterns, not understanding. They were tuned on a small starter set
  (`tests/data/scam_lines.tsv`); grow it with real, consented examples and have a native
  speaker review the Hindi patterns. Known false positive: asking someone to give a
  delivery OTP.
- The AI-voice check misses the newest voice generators and cannot judge a voice heard
  through a loudspeaker and microphone; see "The AI-voice check: what to expect" above.
- Privacy: transcripts and audio are never logged or written to disk.
- **Licence of the voice model: non-commercial.** It is Wav2Vec2-Small-AntiDeepfake from the
  Yamagishi Lab at NII (https://huggingface.co/nii-yamagishilab/wav2vec-small-anti-deepfake,
  CC BY-NC-SA 4.0), fine-tuned by us and converted to ONNX with 8-bit weights; see
  `ml/models/LICENSE-ANTIDEEPFAKE`.
  Fine for a hackathon or research; not for a commercial product. The MIT-licensed AASIST
  model (https://github.com/clovaai/aasist, `ml/models/LICENSE-AASIST`) is still included and
  can be selected with `ANTISPOOF_ONNX=ml/models/aasist.onnx`, but it is far less accurate.
