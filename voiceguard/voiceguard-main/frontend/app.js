// VoiceGuard UI. Talks to the backend only through VoiceGuardClient (contract: docs/API.md):
//   live audio  -> WebSocket /ws/{id}   (the microphone, or a shared browser tab's audio)
//   recordings  -> POST /analyze-file
// Every result carries `verdict`, the call-level outcome; renderVerdict() paints the page from it.
import { VoiceGuardClient } from "./voiceguard-client.js";
import { DEMO } from "./demo.js";

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const RANK = { calm: 0, watch: 1, elevated: 2, high: 3 };
const worst = (...levels) => levels.reduce((a, b) => (RANK[b] > RANK[a] ? b : a), "calm");
const pct = (x) => `${Math.max(0, Math.min(1, x)) * 100}%`;
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const VOICE_NOTE = "The line marks where the voice is called AI-generated; one strongly flagged moment is enough too. "
  + "A flag means \"sounds synthetic to this model\", not proof of a fake. New voice generators and poor audio can fool it.";
const MIC_CAVEAT = "This call was heard through the microphone, where an AI voice often can't be told apart from a real one. ";
const SOURCE_LABEL = { mic: "microphone", tab: "shared tab audio", file: "uploaded recording", demo: "scripted demo" };

// Mirrors the backend defaults until /api/config answers.
let thresholds = { scam_verdict: 0.6, risk_medium: 0.4, voice: 0.5, voice_share: 0.33, voice_strong: 0.95 };
const server = { ready: false, loading: false, wasReady: false, slowStart: false, config: null, models: {} };
let mode = "idle";            // idle | live | demo | upload
let source = "mic";           // mic | tab | file | demo
let lastVerdict = null;       // the verdict on screen
let verdictKey = "";          // changes when the outcome changes, to replay the reveal animation
let chipKey = "";
let lines = 0;
let level = 0;                // live loudness 0..1, drives the animations
let stopping = false;
let startedAt = 0, clock = 0, demoTimer = 0;

const client = new VoiceGuardClient({
  onState: syncControls,
  onResult,
  onReady: (msg) => setModels(msg.models),
  onCleared: () => { resetResults(); menu.toast("Cleared. The call so far was forgotten."); },
  onWarning: (w) => setStatus(w.message),
  onError: onLiveError,
  onEnded: stopLive,                              // e.g. the browser's "Stop sharing" button
  onLevel: (v) => { level = v; },
});

// ---------- status line, controls ----------
function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text;
  el.className = `status ${kind}`.trim();
}

function setPill(state, text) {
  $("listen").dataset.state = state;
  $("listen-text").textContent = text;
  if (state === "idle" || state === "busy") $("listen-time").textContent = "";
}

function syncControls() {
  const consent = $("consent").checked;
  client.consent = consent;
  const idle = mode === "idle" && client.state === "idle";
  const canAnalyse = consent && idle && server.ready && !server.loading;
  $("start").disabled = $("share").disabled = $("file").disabled = !canAnalyse;
  $("demo").disabled = !(consent && idle);
  $("stop").disabled = !((mode === "live" && client.state === "listening") || mode === "demo");
  $("clear").disabled = mode === "upload" || client.state === "connecting" || client.state === "stopping";
  document.body.classList.toggle("is-live", mode === "demo" || (mode === "live" && client.state === "listening"));

  if (mode === "demo") setPill("demo", "Demo running");
  else if (mode === "upload") setPill("busy", "Analysing recording");
  else if (client.state === "connecting") setPill("busy", "Connecting");
  else if (client.state === "stopping") setPill("busy", "Finishing");
  else if (mode === "live" && client.state === "listening") setPill("live", source === "tab" ? "Listening · tab audio" : "Listening · microphone");
  else setPill("idle", "Idle");
}

function readyText() {
  if (!$("consent").checked) return "Tick the box to enable the controls.";
  if (!server.ready) return "The demo script is ready. Live checks unlock when the server answers.";
  if (server.loading) return "The demo script is ready. Live checks unlock when the models have loaded.";
  return "Ready.";
}

function startClock() {
  startedAt = performance.now();
  clearInterval(clock);
  const tick = () => { $("listen-time").textContent = fmtTime((performance.now() - startedAt) / 1000); };
  tick();
  clock = setInterval(tick, 500);
}

function stopClock() {
  clearInterval(clock);
  $("listen-time").textContent = "";
}

// ---------- transcript ----------
function emptyState() {
  const li = document.createElement("li");
  li.className = "empty";
  li.innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-mic"/></svg>'
    + "<span>Nothing transcribed yet. Start listening, upload a recording, or run the demo script.</span>";
  return li;
}

function setTyping(on) {
  const list = $("transcript");
  const dots = list.querySelector(".typing");
  if (on && !dots) {
    const li = document.createElement("li");
    li.className = "typing";
    li.setAttribute("aria-hidden", "true");
    li.innerHTML = "<i></i><i></i><i></i>";
    list.querySelector(".empty")?.remove();
    list.append(li);
  } else if (!on && dots) {
    dots.remove();
    if (!list.children.length) list.append(emptyState());
  }
}

function lineLevel(r) {
  if (r.risk === "high") return "high";
  if (r.risk === "medium") return "elevated";
  return r.reasons.length || r.voice_flag === "elevated" ? "watch" : "ok";
}

function addLine(r, order = 0) {
  const list = $("transcript");
  list.querySelector(".empty")?.remove();
  const li = document.createElement("li");
  li.dataset.level = lineLevel(r);
  li.style.setProperty("--n", Math.min(order, 12));       // staggers lines that arrive together (uploads)
  const time = document.createElement("time");
  time.textContent = fmtTime(r.timestamp);
  if (r.voice_flag === "elevated") {
    const tag = document.createElement("span");
    tag.className = "tag hint";
    tag.textContent = "Voice flagged";
    time.append(tag);
  }
  li.append(time, r.transcript);
  list.insertBefore(li, list.querySelector(".typing"));
  list.scrollTop = list.scrollHeight;
  lines += 1;
  $("t-count").textContent = `${lines} line${lines === 1 ? "" : "s"}`;
}

// A flagged window is only a hint. Its line is called AI-like once the call is, so the transcript
// never says "AI voice" next to a result that says otherwise.
function labelVoiceTags(ai) {
  for (const tag of $("transcript").querySelectorAll(".tag")) {
    tag.textContent = ai ? "AI-like voice" : "Voice flagged";
    tag.classList.toggle("hint", !ai);
  }
}

// ---------- the two checks and the result ----------
function countTo(el, value, format) {
  cancelAnimationFrame(el._raf);
  if (value == null) {
    el._value = null;
    el.textContent = "–";
    return;
  }
  const from = el._value ?? 0, t0 = performance.now(), duration = reduceMotion ? 0 : 700;
  el._value = value;
  const step = (t) => {
    const p = duration ? Math.min(1, (t - t0) / duration) : 1;
    el.textContent = format(from + (value - from) * (1 - (1 - p) ** 3));
    if (p < 1) el._raf = requestAnimationFrame(step);
  };
  step(t0);
}

function renderChips(reasons, final) {
  const key = reasons.join("|") + final;
  if (key === chipKey) return;
  chipKey = key;
  const box = $("reasons");
  if (!reasons.length) {
    const none = document.createElement("span");
    none.className = "empty";
    none.textContent = final ? "None found" : "None yet";
    box.replaceChildren(none);
    return;
  }
  const shown = new Map([...box.querySelectorAll(".chip")].map((c) => [c.textContent, c]));
  box.replaceChildren(...reasons.map((reason) => {
    const old = shown.get(reason);
    if (old) {
      old.style.animation = "none";                 // already on screen: don't pop it again
      return old;
    }
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = reason;
    return chip;
  }));
}

function renderVerdict(v, stage) {
  lastVerdict = v;
  const kind = v.verdict;

  // Voice check: the share of speech windows that look synthetic.
  const hasVoice = v.voice_score != null;
  const voiceLevel = v.ai_voice ? "elevated" : v.voice_flagged > 0 ? "watch" : "calm";
  $("v-panel").dataset.level = voiceLevel;
  countTo($("v-num"), hasVoice ? v.voice_score * 100 : null, (x) => `${Math.round(x)}%`);
  $("v-fill").style.width = hasVoice ? pct(v.voice_score) : "0";
  $("v-cap").textContent = hasVoice
    ? `of the speech looks synthetic (${v.voice_flagged} of ${v.voice_windows} window${v.voice_windows === 1 ? "" : "s"})`
    : "of the speech looks synthetic";
  $("v-label").textContent = !hasVoice ? (v.final ? "Not enough clear speech" : "Waiting for speech")
    : v.ai_voice ? (v.voice_score < thresholds.voice_share ? "Strong AI-voice signs" : "Looks AI-generated")
    : !v.voice_flagged ? "No AI-voice signs"
    : !v.final && v.voice_score >= thresholds.voice_share ? "Flagged, confirming" : "Too little to call AI";
  $("v-note").textContent = (source === "mic" && hasVoice && !v.ai_voice ? MIC_CAVEAT : "") + VOICE_NOTE;
  labelVoiceTags(v.ai_voice);

  // Scam language check: the strongest scam evidence heard during the call.
  const heard = v.speech_seconds > 0;
  const scamLevel = kind === "scam" ? "high" : v.scam_score >= thresholds.risk_medium ? "elevated" : v.reasons.length ? "watch" : "calm";
  $("s-panel").dataset.level = scamLevel;
  countTo($("s-num"), heard ? v.scam_score : null, (x) => x.toFixed(2));
  $("s-fill").style.width = pct(v.scam_score);
  $("s-label").textContent = !heard ? (v.final ? "No speech heard" : "Waiting for speech")
    : scamLevel === "high" ? "Scam tactics matched"
    : scamLevel === "elevated" ? "Several cues"
    : scamLevel === "watch" ? "A weak cue" : "No cues";
  renderChips(v.reasons, v.final);

  // The result: one of the three outcomes (or "not enough to judge").
  const bannerLevel = kind === "scam" ? "high" : kind === "ai_voice" ? "elevated" : kind === "inconclusive" ? "idle"
    : v.reasons.length || v.voice_flagged ? "watch" : "calm";
  const banner = $("banner");
  banner.dataset.level = bannerLevel;
  $("b-icon").setAttribute("href", kind === "ai_voice" ? "#i-voice" : kind === "inconclusive" ? "#i-dots" : bannerLevel === "calm" ? "#i-check" : "#i-alert");
  const stageEl = $("b-stage");
  stageEl.textContent = stage || (source === "demo" ? (v.final ? "Demo result" : "Demo · scripted example") : v.final ? "Final result" : "Live result");
  stageEl.toggleAttribute("data-live", !v.final && !stage);
  if ($("b-title").textContent !== v.title) $("b-title").textContent = v.title;
  if ($("b-text").textContent !== v.message) $("b-text").textContent = v.message;
  for (const li of banner.querySelectorAll(".outcomes li")) li.classList.toggle("on", li.dataset.kind === kind);
  $("b-meta").textContent = `${fmtTime(v.speech_seconds)} of speech in ${v.windows} window${v.windows === 1 ? "" : "s"} · ${SOURCE_LABEL[source]}`;

  const key = `${kind}|${bannerLevel}|${v.final}`;
  if (key !== verdictKey) {                         // the outcome changed: replay the reveal
    verdictKey = key;
    banner.classList.remove("swap");
    void banner.offsetWidth;
    banner.classList.add("swap");
  }
  scene.setTheme(worst(bannerLevel === "idle" ? "calm" : bannerLevel, voiceLevel, scamLevel));
}

function resetResults() {
  lastVerdict = null;
  verdictKey = chipKey = "";
  lines = 0;
  $("transcript").replaceChildren(emptyState());
  $("t-count").textContent = "Waiting";
  for (const k of ["v", "s"]) {
    $(`${k}-panel`).dataset.level = "calm";
    countTo($(`${k}-num`), null);
    $(`${k}-fill`).style.width = "0";
    $(`${k}-label`).textContent = "Waiting for speech";
  }
  $("v-cap").textContent = "of the speech looks synthetic";
  $("v-note").textContent = VOICE_NOTE;
  renderChips([], false);
  const banner = $("banner");
  banner.dataset.level = "idle";
  banner.classList.remove("swap");
  $("b-icon").setAttribute("href", "#i-shield");
  $("b-stage").textContent = "Result";
  $("b-stage").removeAttribute("data-live");
  $("b-title").textContent = "No result yet";
  $("b-text").textContent = "Start a session to see the result here.";
  $("b-meta").textContent = "Results are automated risk indicators, not proof.";
  for (const li of banner.querySelectorAll(".outcomes li")) li.classList.remove("on");
  scene.setTheme("calm");
  if (mode === "live" || mode === "demo") setTyping(true);
}

function onResult(r) {
  if (r.transcript) addLine(r);
  renderVerdict(r.verdict);
}

// ---------- live session ----------
async function startLive(from) {
  if (mode !== "idle") return;
  source = from;
  mode = "live";
  resetResults();
  setTyping(false);
  setStatus(from === "tab" ? 'Pick the tab with the call and turn on "Also share tab audio".' : "Asking for the microphone…");
  syncControls();
  try {
    await client.start({ source: from });
  } catch (e) {
    mode = "idle";
    syncControls();
    setStatus(e.message, "err");
    return;
  }
  if (!$("consent").checked) return void stopLive();      // consent was withdrawn while connecting
  startClock();
  setTyping(true);
  setStatus(from === "tab" ? "Listening to the shared tab. Press Stop for the final result."
    : "Listening. Put the call on speaker. Press Stop for the final result.");
  syncControls();
}

async function stopLive() {
  if (mode !== "live" || stopping) return;
  stopping = true;
  setStatus("Finishing: analysing the last few seconds…");
  const stopped = await client.stop();
  stopping = false;
  finishLive(stopped && stopped.verdict);
}

function finishLive(finalVerdict, lost = false) {
  if (mode !== "live") return;
  mode = "idle";
  level = 0;
  stopClock();
  setTyping(false);
  if (finalVerdict) {
    renderVerdict(finalVerdict);
    setStatus("Stopped. This is the final result.", "ok");
  } else if (lastVerdict) {
    renderVerdict(lastVerdict, "Result so far");
    if (!lost) setStatus("Stopped before the server could finish. Showing the result so far.");
  } else if (!lost) {
    setStatus("Stopped. Nothing was analysed.");
  }
  syncControls();
}

function onLiveError(e) {
  setStatus(e.message, e.fatal ? "err" : "");
  if (e.fatal) finishLive(null, true);
}

// ---------- uploaded recording ----------
async function analyseFile(file) {
  if (mode !== "idle") return;
  const maxMb = server.config?.limits.max_upload_mb;
  if (maxMb && file.size > maxMb * 1024 * 1024) {
    setStatus(`That file is larger than the ${maxMb} MB limit.`, "err");
    return;
  }
  source = "file";
  mode = "upload";
  resetResults();
  $("progress").hidden = false;
  setStatus(`Analysing ${file.name}… long recordings take a while.`);
  syncControls();
  try {
    const report = await client.analyzeFile(file);
    report.results.filter((r) => r.transcript).forEach(addLine);
    renderVerdict(report.verdict);
    setStatus(`Done: ${fmtTime(report.duration)} of audio analysed.`, "ok");
  } catch (e) {
    setStatus(e.message, "err");
  } finally {
    $("progress").hidden = true;
    mode = "idle";
    syncControls();
  }
}

// ---------- scripted demo (no server needed) ----------
function runDemo() {
  if (mode !== "idle") return;
  source = "demo";
  mode = "demo";
  resetResults();
  startClock();
  setStatus("Running the scripted demo. These are example results, not a real analysis.");
  syncControls();
  let i = 0;
  const step = () => {
    if (mode !== "demo") return;
    onResult(DEMO.steps[i++]);
    demoTimer = i < DEMO.steps.length ? setTimeout(step, 2600) : setTimeout(() => endDemo(true), 2000);
  };
  demoTimer = setTimeout(step, 900);
}

function endDemo(completed) {
  if (mode !== "demo") return;
  clearTimeout(demoTimer);
  mode = "idle";
  stopClock();
  setTyping(false);
  if (completed) renderVerdict(DEMO.final);
  else if (lastVerdict) renderVerdict(lastVerdict, "Demo stopped");
  setStatus(completed ? "Demo finished. That was a scripted example, not a real call." : "Demo stopped.");
  syncControls();
}

// ---------- buttons ----------
$("start").addEventListener("click", () => startLive("mic"));
$("share").addEventListener("click", () => startLive("tab"));
$("share").hidden = !VoiceGuardClient.canShareAudio();
$("demo").addEventListener("click", runDemo);
$("stop").addEventListener("click", () => (mode === "demo" ? endDemo(false) : stopLive()));
$("file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  e.target.value = "";                              // so the same file can be chosen again
  if (file) analyseFile(file);
});
$("clear").addEventListener("click", () => {
  if (mode === "live" && client.state === "listening") {
    client.clear();                                 // the server answers "cleared" (see onCleared)
    return;
  }
  if (mode === "demo") endDemo(false);
  resetResults();
  setStatus(readyText());
  menu.toast("Cleared.");
});
$("consent").addEventListener("change", () => {
  if (!$("consent").checked) {                      // consent withdrawn: stop listening at once
    if (mode === "live" && client.state === "listening") stopLive();
    else if (mode === "demo") endDemo(false);
  }
  syncControls();
  if (mode === "idle") setStatus(readyText());
});
addEventListener("pagehide", () => client.stop({ timeoutMs: 0 }));

// ---------- server connection ----------
function setConn(state, text) {
  $("conn").dataset.state = state;
  $("conn-text").textContent = text;
}

function setBadge(text, state = "") {
  const badge = $("mode");
  badge.textContent = text;
  badge.dataset.state = state;
  $("d-status").textContent = text;
}

function setModels(models) {
  const wasBlocked = !server.ready || server.loading;
  server.models = Object.fromEntries(Object.entries(models || {}).map(
    ([k, v]) => [k, typeof v === "string" ? { ...server.models[k], state: v } : v]));
  const states = Object.values(server.models).map((m) => m.state);
  server.ready = server.wasReady = true;
  server.loading = states.some((s) => s === "loading" || s === "not_loaded");
  setConn("ready", "Online");
  const speech = server.models.transcription?.state, voice = server.models.anti_spoof?.state;
  if (server.loading) setBadge("Loading the models…", "loading");
  else if (speech !== "ready") setBadge("Speech-to-text is unavailable", "offline");
  else if (voice !== "ready") setBadge("Speech-to-text ready · voice check off", "loading");
  else setBadge("Speech-to-text ready · voice check ready");
  syncControls();
  if (wasBlocked && !server.loading) {
    if (mode === "idle") setStatus(readyText());
    if (server.slowStart) menu.toast("The server is online. Live checks are ready.");
    server.slowStart = false;
  }
}

function applyConfig(config) {
  server.config = config;
  thresholds = { ...thresholds, ...config.thresholds };
  $("v-tick").style.left = pct(thresholds.voice_share);
  $("s-tick").style.left = pct(thresholds.scam_verdict);
  $("disclaimer").textContent = config.messages.disclaimer;
  $("d-about").textContent = `VoiceGuard ${config.version}`;
  $("d-thresholds").textContent = `Scam at ${thresholds.scam_verdict.toFixed(2)} · AI voice at ${Math.round(thresholds.voice_share * 100)}%`;
}

// Free hosts sleep when idle and take up to a minute to wake, so keep trying and say so.
async function monitor() {
  for (let failures = 0, downSince = 0; ;) {
    let delay = 30000;
    if (!document.hidden || !server.ready) {
      try {
        // First contact: ask for config and status together, saving a round trip.
        const [config, status] = await Promise.all([server.config || client.getConfig(), client.getStatus()]);
        if (!server.config) applyConfig(config);
        setModels(status.models);
        failures = 0;
        downSince = 0;
        if (server.loading) delay = 3000;
      } catch {
        failures += 1;
        const changed = server.ready || failures === 1;
        server.ready = false;
        server.slowStart = true;
        downSince = downSince || Date.now();
        // A sleeping free host, reached over a slow link, can need a few minutes: keep saying so.
        const gone = Date.now() - downSince > (server.wasReady ? 45000 : 240000);
        setConn(gone ? "offline" : "connecting", gone ? "Offline" : "Connecting");
        setBadge(gone ? "Server not reachable · the demo still works" : "Waking the server… this can take a minute or two", gone ? "offline" : "loading");
        syncControls();
        if (changed && mode === "idle") setStatus(readyText());
        delay = Math.min(2000 + failures * 1000, 8000);
      }
    }
    await sleep(delay);
  }
}

// ---------- menu, dialogs, toast ----------
const menu = (() => {
  const btn = $("menu-btn"), drawer = $("drawer"), backdrop = $("drawer-backdrop"), modal = $("modal"), toastEl = $("toast");
  let toastTimer = 0, returnFocus = null;

  function toast(text) {
    toastEl.textContent = text;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 3200);
  }

  function setDrawer(open) {
    btn.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", String(open));
    btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    drawer.classList.toggle("open", open);
    drawer.inert = !open;
    backdrop.classList.toggle("open", open);
    if (open) $("drawer-close").focus({ preventScroll: true });
    else if (drawer.contains(document.activeElement)) btn.focus({ preventScroll: true });
  }

  function openModal(title, html) {
    returnFocus = btn;
    $("modal-title").textContent = title;
    $("modal-body").innerHTML = html;
    modal.classList.add("open");
    modal.inert = false;
    $("modal-ok").focus({ preventScroll: true });
  }

  function closeModal() {
    if (!modal.classList.contains("open")) return;
    modal.classList.remove("open");
    modal.inert = true;
    returnFocus?.focus({ preventScroll: true });
  }

  const modelLine = (m) => (m ? `${esc(m.state.replace("_", " "))}${m.detail ? ` (${esc(m.detail)})` : ""}` : "unknown");
  const session = () => (mode === "live" ? `Listening through the ${SOURCE_LABEL[source]}` : mode === "demo" ? "Running the scripted demo"
    : mode === "upload" ? "Analysing a recording" : "Idle");

  const pages = {
    status: () => ["Protection status", `
      <dl class="facts">
        <dt>Server</dt><dd>${server.ready ? `Online${server.config ? ` (version ${esc(server.config.version)})` : ""}` : "Not reachable"}</dd>
        <dt>Speech-to-text</dt><dd>${modelLine(server.models.transcription)}</dd>
        <dt>Voice check</dt><dd>${modelLine(server.models.anti_spoof)}</dd>
        <dt>Session</dt><dd>${session()}</dd>
      </dl>
      <p class="fine">The models load in the background after the server starts. A free host sleeps when idle and takes up to a minute to wake.</p>`],
    thresholds: () => ["Detection thresholds", `
      <p>The server decides the result; this page only displays it.</p>
      <ul>
        <li><strong>Scam call:</strong> the evidence score reaches <strong>${thresholds.scam_verdict.toFixed(2)}</strong>. Each tactic heard adds to it, and one everyday phrase never convicts on its own.</li>
        <li><strong>AI-generated voice:</strong> at least <strong>${Math.round(thresholds.voice_share * 100)}%</strong> of the speech windows score ${thresholds.voice.toFixed(2)} or more (and at least two of them)${thresholds.voice_strong <= 1 ? `, or any one window scores <strong>${thresholds.voice_strong.toFixed(2)}</strong> or more` : ""}.</li>
      </ul>
      <p class="fine">These are set on the server (VERDICT_SCAM_THRESHOLD, VOICE_THRESHOLD, VOICE_MIN_SHARE, VOICE_STRONG), not in the browser.</p>`],
    audio: () => ["Audio sources", `
      <ul>
        <li><strong>Microphone:</strong> for a phone call on speaker. Good for checking what is said. Room echo and noise hide what marks a voice as synthetic, so an AI voice usually goes unnoticed this way.</li>
        <li><strong>Share tab audio:</strong> for a call taken in this browser (WhatsApp Web, Meet, Zoom). The sound arrives unchanged, so the AI-voice check works. Chrome or Edge on a computer; tick "Also share tab audio".</li>
        <li><strong>Upload recording:</strong> any common audio file. Also reliable for the AI-voice check.</li>
      </ul>`],
    privacy: () => ["Privacy and retention", `
      <p>${esc(server.config?.messages.privacy || "Audio is streamed to the VoiceGuard server for analysis, held in memory only while this session is open, and never stored.")}</p>
      <ul>
        <li><strong>Consent first:</strong> nothing is captured until you tick the consent box, and unticking it stops the session.</li>
        <li><strong>Audio never kept:</strong> audio and transcripts are never written to disk or logged, and Clear forgets the call so far. If the server keeps a history, it saves only the result and its scores, for 10 days, and erases them soon after.</li>
        <li><strong>No voiceprints:</strong> the voice check scores short windows of sound; it does not identify the speaker.</li>
      </ul>`],
    emergency: () => ["Fraud helplines", `
      <p>If you think a call is a scam:</p>
      <ul>
        <li><strong>Hang up.</strong> Pressure, threats and secrecy are the scam. Ending the call is the safest first step.</li>
        <li><strong>Call 1930</strong>, India's National Cyber Crime Helpline, straight away if money has moved.</li>
        <li><strong>Report it</strong> at <a href="https://cybercrime.gov.in" target="_blank" rel="noopener">cybercrime.gov.in</a>.</li>
        <li><strong>Call your bank</strong> on the number printed on your card to block the account.</li>
      </ul>
      <p class="fine">VoiceGuard only advises. It does not contact anyone for you.</p>`],
    about: () => [`About VoiceGuard${server.config ? ` ${server.config.version}` : ""}`, `
      <p>A prototype that listens to a call and gives one of three results: not a scam call, an AI-generated voice that is likely not a scam, or a scam call.</p>
      <ul>
        <li><strong>Scam language check:</strong> speech-to-text, then rules for known scam tactics (digital arrest threats, OTP requests, secrecy, urgency) in English, Hindi and Hinglish.</li>
        <li><strong>Voice check:</strong> an acoustic model that scores each few seconds of speech for signs of synthesis.</li>
      </ul>
      <p class="fine">${esc(server.config?.messages.disclaimer || "Results are automated risk indicators, not proof. When in doubt, hang up and verify.")}</p>`],
  };

  btn.addEventListener("click", () => setDrawer(!drawer.classList.contains("open")));
  $("drawer-close").addEventListener("click", () => setDrawer(false));
  backdrop.addEventListener("click", () => setDrawer(false));
  $("modal-close").addEventListener("click", closeModal);
  $("modal-ok").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (modal.classList.contains("open")) closeModal();
    else if (drawer.classList.contains("open")) setDrawer(false);
  });
  for (const item of document.querySelectorAll(".drawer-item")) {
    item.addEventListener("click", () => {
      setDrawer(false);
      openModal(...pages[item.dataset.action]());
    });
  }
  return { toast };
})();

// ---------- animation: the wave background and the level meter ----------
const scene = (() => {
  const bg = $("bg"), ctx = bg.getContext("2d"), viz = $("viz"), vctx = viz.getContext("2d");
  const GLOW = { calm: [38, 166, 140], watch: [235, 190, 50], elevated: [255, 125, 35], high: [255, 45, 105] };
  const INK = { calm: "#1c8468", watch: "#bc7715", elevated: "#e8590c", high: "#c4123f" };
  let theme = "calm", colour = GLOW.calm.slice(), W = 0, H = 0, vw = 0, vh = 0, last = 0, energy = 0, loud = 0, travel = 0;

  function setTheme(name) {
    theme = GLOW[name] ? name : "calm";
    document.body.dataset.level = theme;
    for (const layer of document.querySelectorAll("#bgwrap div")) layer.classList.toggle("on", layer.dataset.t === theme);
    if (reduceMotion) {
      colour = GLOW[theme].slice();
      draw(0);
    }
  }

  function size() {
    const d = Math.min(devicePixelRatio || 1, 2);
    W = innerWidth;
    H = innerHeight;
    bg.width = W * d;
    bg.height = H * d;
    ctx.setTransform(d, 0, 0, d, 0, 0);
    const box = viz.getBoundingClientRect();
    vw = box.width;
    vh = box.height;
    viz.width = vw * d;
    viz.height = vh * d;
    vctx.setTransform(d, 0, 0, d, 0, 0);
    if (reduceMotion) draw(0);
  }

  function wave(rgb, k, speed, phase, amp, alpha, width) {
    const mid = H * 0.72;
    ctx.beginPath();
    for (let x = 0; x <= W + 8; x += 8) {
      const y = mid + Math.sin(k * (x - travel * speed) + phase) * amp + Math.cos(k * 0.5 * (x + travel * 0.4 * speed)) * amp * 0.45;
      if (x) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    }
    ctx.lineWidth = width * 4;                        // a wide faint pass first: a glow without costly blur
    ctx.strokeStyle = `rgba(${rgb},${alpha * 0.22})`;
    ctx.stroke();
    ctx.lineWidth = width;
    ctx.strokeStyle = `rgba(${rgb},${alpha})`;
    ctx.stroke();
  }

  function draw(t) {
    const rgb = colour.map(Math.round).join(",");
    const amp = 16 + energy * 26;
    ctx.clearRect(0, 0, W, H);
    ctx.lineCap = ctx.lineJoin = "round";
    wave(rgb, (2 * Math.PI) / 620, 1, 0, amp, 0.26, 2.4);
    wave(rgb, (2 * Math.PI) / 430, -0.8, 1.8, amp * 0.75, 0.4, 1.8);
    wave(rgb, (2 * Math.PI) / 300, 1.25, 3.2, amp * 0.5, 0.18, 1.2);

    // Level meter: bars follow the real loudness of the audio being analysed.
    vctx.clearRect(0, 0, vw, vh);
    const active = mode === "live" || mode === "demo";
    const count = Math.max(8, Math.floor(vw / 7)), gap = vw / count, mid = vh / 2;
    vctx.lineCap = "round";
    vctx.lineWidth = 3;
    vctx.strokeStyle = active ? INK[theme] : "#9db5bf";
    for (let i = 0; i < count; i++) {
      const shape = 0.25 + 0.75 * Math.abs(Math.sin(i * 0.37 + t * 0.004)) * (0.6 + 0.4 * Math.sin(i * 0.11 - t * 0.0023));
      const idle = 0.5 + 0.5 * Math.sin(i * 0.3 - t * 0.002);
      const h = active ? 1.5 + (mid - 4) * loud * shape : 1 + idle * 1.5;
      const x = gap * (i + 0.5);
      vctx.globalAlpha = active ? 0.35 + 0.65 * shape : 0.5 + idle * 0.5;
      vctx.beginPath();
      vctx.moveTo(x, mid - h);
      vctx.lineTo(x, mid + h);
      vctx.stroke();
    }
    vctx.globalAlpha = 1;
  }

  function frame(t) {
    const dt = Math.min((t - last) / 1000, 0.05);
    last = t;
    const active = mode === "live" || mode === "demo";
    // The demo has no audio, so it gets a speech-like pulse of its own.
    const heard = mode === "demo" ? 0.45 + 0.4 * Math.abs(Math.sin(t * 0.006) * Math.sin(t * 0.0017)) : Math.min(1, level * 7);
    loud += ((active ? heard : 0) - loud) * Math.min(1, dt * 12);
    energy += ((active ? 0.35 + loud * 0.65 : 0) - energy) * Math.min(1, dt * 2.5);
    travel += 42 * (1 + energy * 0.6) * dt;
    const ease = Math.min(1, dt * 2.5);
    colour = colour.map((v, i) => v + (GLOW[theme][i] - v) * ease);
    draw(t);
    requestAnimationFrame(frame);
  }

  addEventListener("resize", size);
  new ResizeObserver(size).observe(viz);
  size();
  if (!reduceMotion) requestAnimationFrame(frame);
  return { setTheme };
})();

// ---------- start ----------
resetResults();
syncControls();
setStatus(readyText());
monitor();
