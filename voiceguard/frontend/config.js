// Where the VoiceGuard backend lives.
//
//   ""  = the same server that serves this page (running `uvicorn backend.app:app` locally,
//         or the Docker image, which serves this UI itself)
//
// When this folder is hosted separately (e.g. on Vercel), put your backend's HTTPS URL here:
//
//   window.VOICEGUARD_API = "https://your-backend.example.com";
//
// The WebSocket address (wss://...) is derived from it automatically.
// On your own computer (run.bat) the page uses the local server; anywhere else, the hosted one.
window.VOICEGUARD_API = ["localhost", "127.0.0.1"].includes(location.hostname) ? "" : "https://voiceguard-api-3x3m.onrender.com";

// Open the DNS + TLS connection to the backend and start waking it while the rest of the page
// loads: a sleeping free host needs every second it can get.
(function warmUp(api) {
  if (!api) return;
  const link = document.createElement("link");
  link.rel = "preconnect";
  link.href = api;
  link.crossOrigin = "anonymous";
  document.head.appendChild(link);
  fetch(api.replace(/\/+$/, "") + "/health", { cache: "no-store" }).catch(() => {});
})(window.VOICEGUARD_API);
