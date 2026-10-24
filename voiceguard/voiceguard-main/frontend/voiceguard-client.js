// VoiceGuard client: the browser half of the API contract in docs/API.md.
// Framework-agnostic ES module; works from plain JS or React.
//
//   const vg = new VoiceGuardClient({ onResult, onState, onError, onWarning });
//   await vg.start();                      // mic -> PCM16 -> WebSocket
//   await vg.start({ source: "tab" });     // or: the audio of a browser tab the user picks
//   onResult(result)                       // every few seconds; result.verdict is the running verdict
//   vg.clear();                            // forget the call so far
//   const { verdict } = await vg.stop();   // mic off at once; resolves with the final verdict
//   const report = await vg.analyzeFile(file);   // report.verdict for a recording
//
// A verdict is { verdict: "safe" | "ai_voice" | "scam" | "inconclusive", title, message, ... }.
// The backend URL comes from opts.baseUrl, else window.VOICEGUARD_API (config.js), else this origin.

export class VoiceGuardError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "VoiceGuardError";
    this.code = code;
  }
}

const noop = () => {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// RMS of a PCM16 frame, 0..1.
function frameLevel(buffer) {
  const pcm = new Int16Array(buffer);
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
  return pcm.length ? Math.sqrt(sum / pcm.length) / 32768 : 0;
}

// An anonymous id for this browser, so the server can show it its own history. Not a login:
// clearing site data starts a new history.
function browserId() {
  try {
    let id = localStorage.getItem("voiceguard-client-id");
    if (!id) {
      id = newSessionId().replace(/[^A-Za-z0-9_-]/g, "");
      localStorage.setItem("voiceguard-client-id", id);
    }
    return id;
  } catch {
    return null;                                        // storage blocked: calls are kept without an owner
  }
}

function newSessionId() {
  if (globalThis.crypto && crypto.randomUUID) return crypto.randomUUID();
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}

export class VoiceGuardClient {
  /**
   * @param {object} opts
   * @param {string} [opts.baseUrl]  API origin, e.g. "https://api.example.com" ("" = same origin)
   * @param {string} [opts.wsBase]   WebSocket origin; derived from baseUrl / the page when omitted
   */
  constructor(opts = {}) {
    const configured = typeof window !== "undefined" ? window.VOICEGUARD_API : "";
    this.baseUrl = (opts.baseUrl ?? configured ?? "").replace(/\/+$/, "");
    this.wsBase = opts.wsBase ? opts.wsBase.replace(/\/+$/, "") : null;
    this.onResult = opts.onResult || noop;     // (result) per analysed window
    this.onState = opts.onState || noop;       // ("idle" | "connecting" | "listening" | "stopping")
    this.onError = opts.onError || noop;       // ({ code, message, fatal })
    this.onWarning = opts.onWarning || noop;   // ({ code, message, ... })
    this.onReady = opts.onReady || noop;       // (readyMessage)
    this.onCleared = opts.onCleared || noop;
    this.onEnded = opts.onEnded || noop;       // the audio source stopped by itself (e.g. "Stop sharing")
    this.onLevel = opts.onLevel || null;       // (0..1) loudness of each ~128 ms frame, for a level meter
    this.state = "idle";
    this.sessionId = null;
    this._ws = null;
    this._stream = null;
    this._ctx = null;
    this._node = null;
    this._ping = null;
    this._stopped = null;
    this._fatalSeen = false;
    this.clientId = browserId();
    this.consent = false;                      // set by the page from its consent box; stored with each call
  }

  // ---- REST ---------------------------------------------------------------------
  async _json(path, init, timeoutMs = 0) {
    let res;
    // On a weak connection a request can hang without ever failing: give up and let the caller retry.
    const abort = timeoutMs ? new AbortController() : null;
    const timer = abort && setTimeout(() => abort.abort(), timeoutMs);
    try {
      res = await fetch(this.baseUrl + path, abort ? { ...init, signal: abort.signal } : init);
    } catch (e) {
      clearTimeout(timer);
      throw new VoiceGuardError("network_error", "Can't reach the VoiceGuard server.");
    }
    const body = await res.json().catch(() => ({}));
    clearTimeout(timer);
    if (!res.ok) {
      const detail = typeof body.detail === "string" ? body.detail : res.statusText;
      throw new VoiceGuardError(body.code || `http_${res.status}`, detail || `HTTP ${res.status}`);
    }
    return body;
  }

  _headers() { return this.clientId ? { "X-VoiceGuard-Client": this.clientId } : {}; }

  getConfig() { return this._json("/api/config", undefined, 25000); }

  // Stored calls (only when the server has a database). See docs/STORAGE.md.
  getHistory({ limit = 20, offset = 0 } = {}) {
    return this._json(`/api/history?limit=${limit}&offset=${offset}`, { headers: this._headers() }, 25000);
  }
  getCall(uid) { return this._json(`/api/history/${uid}`, { headers: this._headers() }, 25000); }
  deleteCall(uid) { return this._json(`/api/history/${uid}`, { method: "DELETE", headers: this._headers() }, 25000); }
  sendFeedback(uid, { isCorrect, actualLabel = null, comment = null }) {
    return this._json(`/api/history/${uid}/feedback`, {
      method: "POST",
      headers: { ...this._headers(), "Content-Type": "application/json" },
      body: JSON.stringify({ is_correct: isCorrect, actual_label: actualLabel, comment }),
    }, 25000);
  }
  getStatus() { return this._json("/api/status", undefined, 25000); }

  analyzeFile(file) {
    const form = new FormData();
    form.append("file", file, file.name || "audio");
    return this._json(`/analyze-file?consent=${this.consent ? 1 : 0}`, { method: "POST", body: form, headers: this._headers() });
  }

  // ---- live stream --------------------------------------------------------------
  /** True when this browser can share a tab's audio (Chrome and Edge on a computer). */
  static canShareAudio() {
    return typeof navigator !== "undefined" && !!navigator.mediaDevices
      && typeof navigator.mediaDevices.getDisplayMedia === "function";
  }

  /**
   * Starts a live check.
   * source "mic" (default) listens through the microphone. source "tab" asks the browser to
   * share a tab's audio (a call in WhatsApp Web, Meet, ...): the sound reaches the server
   * unchanged, which the AI-voice check needs. Through a speaker and a microphone, room echo
   * and noise hide most of what marks a voice as synthetic.
   */
  async start({ source = "mic" } = {}) {
    if (this.state !== "idle") return;
    this._fatalSeen = false;
    this._setState("connecting");
    try {
      this._stream = source === "tab" ? await this._shareAudio() : await this._openMic();
      const [track] = this._stream.getAudioTracks();
      track.addEventListener("ended", () => { if (this.state === "listening") this.onEnded(); });
      const input = await this._createAudioGraph(this._stream);
      await this._openSocket(this._ctx.sampleRate, source);
      this._node.port.onmessage = (e) => {
        if (this.onLevel) this.onLevel(frameLevel(e.data));
        if (this._ws && this._ws.readyState === WebSocket.OPEN) this._ws.send(e.data);
      };
      input.connect(this._node);
      this._ping = setInterval(() => this._sendJson({ type: "ping" }), 15000);
      this._setState("listening");
    } catch (e) {
      this._teardown();
      this._setState("idle");
      throw e instanceof VoiceGuardError ? e : new VoiceGuardError("start_failed", String(e && e.message || e));
    }
  }

  async _openMic() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new VoiceGuardError("mic_unsupported", "Microphone access needs HTTPS (or localhost) and a modern browser.");
    }
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
    } catch (e) {
      const denied = e && (e.name === "NotAllowedError" || e.name === "SecurityError");
      throw new VoiceGuardError(denied ? "mic_denied" : "mic_unavailable",
        denied ? "Microphone permission was denied." : "No microphone is available.");
    }
  }

  async _shareAudio() {
    if (!VoiceGuardClient.canShareAudio()) {
      throw new VoiceGuardError("share_unsupported", "This browser can't share tab audio. Use Chrome or Edge on a computer.");
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,                                  // browsers only offer audio along with a tab or screen
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
    } catch (e) {
      throw new VoiceGuardError("share_cancelled", "Nothing was shared.");
    }
    if (!stream.getAudioTracks().length) {
      stream.getTracks().forEach((t) => t.stop());
      throw new VoiceGuardError("share_no_audio", "No audio was shared. Pick the tab with the call and turn on \"Also share tab audio\".");
    }
    stream.getVideoTracks().forEach((t) => t.stop());   // only the sound is needed
    return stream;
  }

  async _createAudioGraph(stream) {
    // Ask for 16 kHz so the browser resamples; if this browser can't connect a mic to a
    // 16 kHz context (Firefox), fall back to the native rate and let the server resample.
    let ctx, source;
    try {
      ctx = new AudioContext({ sampleRate: 16000 });
      source = ctx.createMediaStreamSource(stream);
    } catch (e) {
      if (ctx) ctx.close().catch(noop);
      ctx = new AudioContext();
      source = ctx.createMediaStreamSource(stream);
    }
    this._ctx = ctx;
    await ctx.audioWorklet.addModule(new URL("./pcm-worklet.js", import.meta.url));
    this._node = new AudioWorkletNode(ctx, "pcm", {
      channelCount: 1, channelCountMode: "explicit",          // stereo tab audio is mixed down to mono
      processorOptions: { chunkSize: Math.round(ctx.sampleRate * 0.128) },
    });
    const mute = ctx.createGain();               // keep the graph pulled without playing audio back
    mute.gain.value = 0;
    this._node.connect(mute).connect(ctx.destination);
    if (ctx.state === "suspended") await ctx.resume();
    return source;
  }

  _wsUrl(rate, source = "mic") {
    let origin = this.wsBase;
    if (!origin) {
      const base = new URL(this.baseUrl || location.origin, location.href);
      origin = (base.protocol === "https:" ? "wss://" : "ws://") + base.host;
    }
    const q = new URLSearchParams({ sample_rate: Math.round(rate), source, consent: this.consent ? 1 : 0 });
    if (this.clientId) q.set("client", this.clientId);
    return `${origin}/ws/${encodeURIComponent(this.sessionId)}?${q}`;
  }

  _openSocket(rate, source) {
    return new Promise((resolve, reject) => {
      this.sessionId = newSessionId();
      const ws = new WebSocket(this._wsUrl(rate, source));
      ws.binaryType = "arraybuffer";
      this._ws = ws;
      let ready = false;
      const timer = setTimeout(() => {
        if (!ready) { ws.close(); reject(new VoiceGuardError("timeout", "The server did not answer.")); }
      }, 30000);                                          // slow links and waking hosts need a while

      ws.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }
        if (msg.type === "ready" && !ready) { ready = true; clearTimeout(timer); resolve(msg); }
        if (msg.type === "error" && !ready) {
          ready = true; clearTimeout(timer); this._fatalSeen = true;
          reject(new VoiceGuardError(msg.code, msg.message));
          return;
        }
        this._dispatch(msg);
      };
      ws.onclose = (e) => {
        clearTimeout(timer);
        if (!ready) {
          ready = true;
          reject(new VoiceGuardError("connect_failed", e.reason || `Could not connect (code ${e.code}).`));
        }
        this._onSocketClosed(e);
      };
    });
  }

  _dispatch(msg) {
    switch (msg.type) {
      case "ready": this.onReady(msg); break;
      case "result": this.onResult(msg); break;
      case "warning": this.onWarning(msg); break;
      case "cleared": this.onCleared(msg); break;
      case "stopped": if (this._stopped) this._stopped(msg); break;
      case "error":
        if (msg.fatal) this._fatalSeen = true;
        this.onError(msg);
        break;
      default: break;                                   // "pong" and future types
    }
  }

  _onSocketClosed(e) {
    const wasActive = this.state === "listening";
    this._ws = null;
    if (this._stopped) this._stopped(null);
    if (wasActive) {
      this._teardown();
      this._setState("idle");
      if (!this._fatalSeen) {
        this.onError({ code: "disconnected", message: "Connection to the server was lost.", fatal: true });
      }
    }
  }

  _sendJson(obj) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) this._ws.send(JSON.stringify(obj));
  }

  clear() { this._sendJson({ type: "clear" }); }

  /** Stops listening. Resolves with the server's "stopped" message ({ verdict, ... }) or null. */
  async stop({ timeoutMs = 45000 } = {}) {
    if (this.state === "idle" || this.state === "stopping") return null;
    this._setState("stopping");
    this._stopMic();                                    // the mic indicator goes off right away
    const ws = this._ws;
    let stopped = null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      const done = new Promise((resolve) => { this._stopped = resolve; });
      ws.send(JSON.stringify({ type: "stop" }));        // server analyses the last partial window
      stopped = await Promise.race([done, sleep(timeoutMs).then(() => null)]);
    }
    this._teardown();
    this._setState("idle");
    return stopped;
  }

  _stopMic() {
    if (this._stream) this._stream.getTracks().forEach((t) => t.stop());
    this._stream = null;
    if (this._node) { this._node.port.onmessage = null; this._node.disconnect(); }
    this._node = null;
    if (this._ctx) this._ctx.close().catch(noop);
    this._ctx = null;
  }

  _teardown() {
    clearInterval(this._ping);
    this._ping = null;
    this._stopMic();
    this._stopped = null;
    if (this._ws) {
      const ws = this._ws;
      this._ws = null;
      ws.onclose = null;
      ws.onmessage = null;
      if (ws.readyState <= WebSocket.OPEN) ws.close(1000);
    }
  }

  _setState(s) {
    this.state = s;
    this.onState(s);
  }
}
