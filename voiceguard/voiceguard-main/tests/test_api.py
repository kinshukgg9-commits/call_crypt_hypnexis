"""REST endpoints: health, status, config, file upload, CORS and the static UI."""
import numpy as np

from tests.helpers import FakeDetector, tone, wav_bytes


def upload(client, data: bytes, name: str = "clip.wav"):
    return client.post("/analyze-file", files={"file": (name, data, "audio/wav")})


def test_health(make_client):
    client, _, _ = make_client()
    assert client.get("/health").json() == {"ok": True}
    assert client.head("/health").status_code == 200


def test_status_reports_models_and_sessions(make_client):
    client, _, _ = make_client(max_sessions=3)
    body = client.get("/api/status").json()
    assert body["ok"] is True and body["active_sessions"] == 0 and body["max_sessions"] == 3
    assert body["models"]["transcription"]["state"] == "ready"
    assert body["models"]["anti_spoof"] == {"state": "ready", "detail": "fake detector", "error": None}


def test_config_matches_server_settings(make_client):
    client, _, _ = make_client(voice_threshold=0.65)
    body = client.get("/api/config").json()
    assert body["audio"] == {
        "sample_rate": 16000, "encoding": "pcm_s16le", "channels": 1, "window_seconds": 4.0,
        "recommended_frame_ms": 128, "min_input_sample_rate": 8000, "max_input_sample_rate": 192000,
    }
    assert body["thresholds"] == {
        "scam_verdict": 0.6, "risk_high": 0.7, "risk_medium": 0.4, "voice": 0.65, "voice_share": 0.33,
        "voice_strong": 0.95,
    }
    assert set(body["messages"]["verdict"]) == {"safe", "ai_voice", "scam", "inconclusive"}
    assert set(body["messages"]["risk"]) == {"low", "medium", "high"}
    assert set(body["messages"]["voice"]) == {"normal", "elevated", "insufficient_audio", "clipped", "unavailable"}
    assert "1930" in body["messages"]["risk"]["high"]
    assert body["endpoints"]["websocket"] == "/ws/{session_id}"


def test_upload_analyses_every_window(make_client):
    client, _, _ = make_client(texts=["This is the CBI.", "You are under digital arrest.", "Read me the OTP now."])
    r = upload(client, wav_bytes(tone(9.0)))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"].startswith("upload-") and body["filename"] == "clip.wav"
    assert body["duration"] == 9.0 and body["window_seconds"] == 4.0
    assert [w["timestamp"] for w in body["results"]] == [4.0, 8.0, 9.0]
    assert [w["window_index"] for w in body["results"]] == [0, 1, 2]
    assert [w["risk"] for w in body["results"]] == ["low", "high", "high"]
    assert [w["verdict"]["verdict"] for w in body["results"]] == ["safe", "scam", "scam"]
    verdict = body["verdict"]
    assert verdict["verdict"] == "scam" and verdict["final"] is True and verdict["ai_voice"] is False
    assert verdict["reasons"] == ["Known scam script", "Sensitive info request", "Authority impersonation"]
    assert verdict["scam_score"] == 0.9 and verdict["windows"] == 3
    last = body["results"][-1]
    assert last["voice_status"] == "insufficient_audio" and last["voice_score"] is None   # 1 s tail
    assert body["summary"] == {
        "windows": 3,
        "risk": "high",
        "max_scam_score": last["scam_score"],
        "reasons": ["Authority impersonation", "Known scam script", "Sensitive info request"],
        "max_voice_score": 0.1,
        "voice_flag": "normal",
    }


def test_upload_skips_a_sliver_of_tail(make_client):
    client, _, _ = make_client()
    body = upload(client, wav_bytes(tone(8.2))).json()
    assert len(body["results"]) == 2 and body["duration"] == 8.2


def test_upload_short_clip_still_gets_one_result(make_client):
    client, _, _ = make_client()
    body = upload(client, wav_bytes(tone(0.3))).json()
    assert len(body["results"]) == 1
    assert body["results"][0]["voice_flag"] == "insufficient_audio"
    assert body["summary"]["voice_flag"] == "insufficient_audio"


def test_upload_stereo_44k_is_converted(make_client):
    client, _, det = make_client()
    body = upload(client, wav_bytes(tone(4.0, sr=44100), sr=44100, channels=2)).json()
    assert abs(body["duration"] - 4.0) < 0.01
    assert det.lengths and abs(det.lengths[0] - 64000) <= 16


def test_upload_elevated_voice_in_summary(make_client):
    client, _, _ = make_client(detector=FakeDetector(value=0.95))
    body = upload(client, wav_bytes(tone(4.0))).json()
    assert body["summary"]["voice_flag"] == "elevated" and body["summary"]["max_voice_score"] == 0.95


def test_upload_verdicts_for_ai_voice_and_safe(make_client):
    client, _, _ = make_client(texts=["hello, how are you doing today"], detector=FakeDetector(value=0.95))
    verdict = upload(client, wav_bytes(tone(4.0))).json()["verdict"]
    assert verdict["verdict"] == "ai_voice" and verdict["ai_voice"] is True and verdict["reasons"] == []

    client, _, _ = make_client(texts=["hello, how are you doing today"], detector=FakeDetector(value=0.05))
    verdict = upload(client, wav_bytes(tone(4.0))).json()["verdict"]
    assert verdict["verdict"] == "safe" and verdict["title"] == "Not a scam call"
    assert verdict["message"].endswith("No signs of an AI-generated voice were found.")


def test_upload_scam_with_ai_voice_says_both(make_client):
    client, _, _ = make_client(texts=["read me the OTP"], detector=FakeDetector(value=0.95))
    verdict = upload(client, wav_bytes(tone(4.0))).json()["verdict"]
    assert verdict["verdict"] == "scam" and verdict["ai_voice"] is True
    assert "AI-generated" in verdict["message"]


def test_upload_errors_have_detail_and_code(make_client):
    client, _, _ = make_client()
    r = upload(client, b"")
    assert r.status_code == 400 and r.json()["code"] == "empty_file"
    r = upload(client, b"this is not audio at all" * 50, "notes.txt")
    assert r.status_code == 415 and r.json()["code"] == "unsupported_audio"
    r = client.post("/analyze-file")
    assert r.status_code == 422 and r.json()["code"] == "invalid_request" and "file" in r.json()["detail"]


def test_upload_too_large(make_client):
    client, _, _ = make_client(max_upload_mb=0.05)
    r = upload(client, wav_bytes(tone(10.0)))
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"


def test_upload_too_long(make_client):
    client, _, _ = make_client(max_upload_seconds=5)
    r = upload(client, wav_bytes(tone(6.0)))
    assert r.status_code == 413 and r.json()["code"] == "audio_too_long"


def test_upload_of_pure_silence(make_client):
    client, _, _ = make_client()
    body = upload(client, wav_bytes(np.zeros(16000 * 5, np.float32))).json()
    assert body["summary"]["risk"] == "low" and body["summary"]["voice_flag"] == "insufficient_audio"
    assert body["verdict"]["verdict"] == "inconclusive" and body["verdict"]["title"] == "Not enough to judge"


def test_unknown_route_has_error_shape(make_client):
    client, _, _ = make_client()
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found", "code": "http_404"}


def test_cors_allows_configured_origin_only(make_client):
    client, _, _ = make_client(cors_origins=("http://localhost:5173",))
    ok = client.options("/analyze-file", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.get("/api/config", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_cors_is_open_by_default_and_supports_wildcard_subdomains(make_client):
    client, _, _ = make_client()
    r = client.get("/api/config", headers={"Origin": "https://my-app.vercel.app"})
    assert r.headers.get("access-control-allow-origin") == "*"

    client, _, _ = make_client(cors_origins=("https://*.vercel.app",))
    ok = client.get("/api/config", headers={"Origin": "https://voiceguard-git-main-team.vercel.app"})
    assert ok.headers.get("access-control-allow-origin") == "https://voiceguard-git-main-team.vercel.app"
    for origin in ("https://vercel.app.evil.example", "http://x.vercel.app", "https://evil.example/.vercel.app"):
        bad = client.get("/api/config", headers={"Origin": origin})
        assert "access-control-allow-origin" not in bad.headers, origin

    client, _, _ = make_client(cors_origins=())
    off = client.get("/api/config", headers={"Origin": "https://my-app.vercel.app"})
    assert "access-control-allow-origin" not in off.headers


def test_frontend_is_served_at_root(make_client, tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<h1>VoiceGuard</h1>")
    client, _, _ = make_client(frontend_dir=ui)
    r = client.get("/")
    assert r.status_code == 200 and "VoiceGuard" in r.text
    assert r.headers["cache-control"] == "no-cache"          # never a stale client after a redeploy
    again = client.get("/", headers={"if-none-match": r.headers["etag"]})
    assert again.status_code == 304
    assert client.get("/health").json() == {"ok": True}      # API routes still win over the UI mount


def test_root_without_frontend_points_to_docs(make_client):
    client, _, _ = make_client()
    assert client.get("/").json()["docs"] == "/docs"
    assert client.get("/openapi.json").status_code == 200
