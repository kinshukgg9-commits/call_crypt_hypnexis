"""The live-stream protocol, end to end through the WebSocket endpoint."""
import json

import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from tests.helpers import FakeDetector, FakeTranscriber, pcm16, send_audio, tone

RESULT_KEYS = {
    "type", "session_id", "timestamp", "window_index", "duration", "speech_seconds", "transcript",
    "transcript_status", "language", "scam_score", "risk", "reasons", "voice_score", "voice_status",
    "voice_flag", "processing_ms", "verdict",
}
VERDICT_KEYS = {
    "verdict", "title", "message", "final", "scam_score", "reasons", "ai_voice", "voice_score",
    "voice_flagged", "voice_windows", "windows", "speech_seconds",
}


def expect_close(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    return exc.value.code


def test_ready_then_result_with_full_contract(make_client):
    client, _, _ = make_client(texts=["Do not tell anyone and read me the OTP"])
    with client.websocket_connect("/ws/abc-123") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["session_id"] == "abc-123"
        assert ready["sample_rate"] == ready["input_sample_rate"] == 16000
        assert ready["encoding"] == "pcm_s16le" and ready["window_seconds"] == 4.0
        assert set(ready["models"]) == {"transcription", "anti_spoof"}

        send_audio(ws, tone(4.0))
        msg = ws.receive_json()
    assert set(msg) == RESULT_KEYS
    assert msg["type"] == "result" and msg["session_id"] == "abc-123"
    assert msg["timestamp"] == 4.0 and msg["window_index"] == 0 and msg["duration"] == 4.0
    assert msg["transcript"] == "Do not tell anyone and read me the OTP"
    assert msg["transcript_status"] == "ok" and msg["language"] == "en"
    assert msg["scam_score"] == 0.78 and msg["risk"] == "high"
    assert msg["reasons"] == ["Sensitive info request", "Secrecy"]
    assert msg["voice_score"] == 0.1 and msg["voice_status"] == "ok" and msg["voice_flag"] == "normal"
    assert isinstance(msg["processing_ms"], int)
    verdict = msg["verdict"]
    assert set(verdict) == VERDICT_KEYS
    assert verdict["verdict"] == "scam" and verdict["title"] == "Scam call detected"
    assert verdict["final"] is False and verdict["ai_voice"] is False and verdict["scam_score"] == 0.78


def test_transcript_context_spans_windows(make_client):
    client, _, _ = make_client(texts=["Do not tell anyone", "and read me the OTP"])
    with client.websocket_connect("/ws/ctx") as ws:
        ws.receive_json()
        send_audio(ws, tone(8.0))
        first, second = ws.receive_json(), ws.receive_json()
    assert (first["risk"], first["reasons"]) == ("medium", ["Secrecy"])
    assert first["verdict"]["verdict"] == "safe"            # one weak cue is not a scam verdict
    assert second["risk"] == "high" and second["timestamp"] == 8.0 and second["window_index"] == 1
    assert second["reasons"] == ["Sensitive info request", "Secrecy"]
    assert second["verdict"]["verdict"] == "scam"


def test_context_is_limited_to_recent_windows(make_client):
    client, _, _ = make_client(texts=["Do not tell anyone", "", ""], context_windows=2)
    with client.websocket_connect("/ws/ctx2") as ws:
        ws.receive_json()
        send_audio(ws, tone(12.0))
        risks = [ws.receive_json()["risk"] for _ in range(3)]
    assert risks == ["medium", "medium", "low"]   # the cue ages out after 2 windows


def test_odd_sized_frames_are_reassembled(make_client):
    client, _, det = make_client()
    data = pcm16(tone(4.0))
    with client.websocket_connect("/ws/odd") as ws:
        ws.receive_json()
        for i in range(0, len(data), 1001):            # odd sizes split int16 samples
            ws.send_bytes(data[i:i + 1001])
        msg = ws.receive_json()
    assert msg["type"] == "result" and msg["voice_status"] == "ok"
    assert det.lengths == [64000]


def test_other_input_sample_rates_are_resampled(make_client):
    client, _, det = make_client()
    with client.websocket_connect("/ws/hi-rate?sample_rate=48000") as ws:
        ready = ws.receive_json()
        assert ready["input_sample_rate"] == 48000 and ready["sample_rate"] == 16000
        send_audio(ws, tone(4.0, sr=48000))
        msg = ws.receive_json()
    assert msg["timestamp"] == 4.0
    assert det.lengths == [64000]


@pytest.mark.parametrize("rate", ["abc", "100", "999999", "inf", "nan"])
def test_invalid_sample_rate_is_rejected(make_client, rate):
    client, _, _ = make_client()
    with client.websocket_connect(f"/ws/rate?sample_rate={rate}") as ws:
        err = ws.receive_json()
        assert err == {"type": "error", "code": "invalid_sample_rate", "message": err["message"], "fatal": True}
        assert expect_close(ws) == 4400


def test_invalid_session_id_is_rejected(make_client):
    client, _, _ = make_client()
    with client.websocket_connect("/ws/" + "x" * 65) as ws:
        assert ws.receive_json()["code"] == "invalid_session_id"
        assert expect_close(ws) == 4400


def test_duplicate_session_id_is_rejected(make_client):
    client, _, _ = make_client()
    with client.websocket_connect("/ws/same") as first:
        first.receive_json()
        with client.websocket_connect("/ws/same") as second:
            assert second.receive_json()["code"] == "session_active"
            assert expect_close(second) == 4409
        first.send_text(json.dumps({"type": "ping"}))
        assert first.receive_json() == {"type": "pong"}     # the first session is unaffected


def test_server_busy_when_session_limit_reached(make_client):
    client, _, _ = make_client(max_sessions=1)
    with client.websocket_connect("/ws/one") as first:
        first.receive_json()
        with client.websocket_connect("/ws/two") as second:
            assert second.receive_json()["code"] == "server_busy"
            assert expect_close(second) == 4429
    # once the first session ends, a new one is accepted
    with client.websocket_connect("/ws/three") as third:
        assert third.receive_json()["type"] == "ready"


def test_ping_pong_and_bad_text_messages(make_client):
    client, _, _ = make_client()
    with client.websocket_connect("/ws/ctl") as ws:
        ws.receive_json()
        ws.send_text("not json")
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "bad_message" and err["fatal"] is False
        ws.send_text(json.dumps({"type": "dance"}))
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}


def test_stop_flushes_partial_window_then_closes(make_client):
    client, _, _ = make_client(texts=["transfer the money to my account"])
    with client.websocket_connect("/ws/stop") as ws:
        ws.receive_json()
        send_audio(ws, tone(2.5))
        ws.send_text(json.dumps({"type": "stop"}))
        result = ws.receive_json()
        stopped = ws.receive_json()
        code = expect_close(ws)
    assert result["type"] == "result" and result["timestamp"] == 2.5 and result["risk"] == "medium"
    assert result["voice_status"] == "ok"
    assert (stopped["type"], stopped["windows_analysed"], stopped["dropped_windows"]) == ("stopped", 1, 0)
    final = stopped["verdict"]
    assert final["final"] is True and final["verdict"] == "safe" and final["title"] == "Not a scam call"
    assert final["reasons"] == ["Money movement"] and final["windows"] == 1
    assert code == 1000


def test_stop_skips_a_tiny_tail(make_client):
    client, transcriber, _ = make_client()
    with client.websocket_connect("/ws/stop2") as ws:
        ws.receive_json()
        send_audio(ws, tone(0.2))
        ws.send_text(json.dumps({"type": "stop"}))
        stopped = ws.receive_json()
        assert (stopped["type"], stopped["windows_analysed"]) == ("stopped", 0)
        assert stopped["verdict"]["verdict"] == "inconclusive" and stopped["verdict"]["final"] is True
        assert expect_close(ws) == 1000
    assert transcriber.calls == 0


def test_clear_resets_context_but_keeps_the_session_clock(make_client):
    client, _, _ = make_client(texts=["Do not tell anyone", "and read me the OTP"])
    with client.websocket_connect("/ws/clear") as ws:
        ws.receive_json()
        send_audio(ws, tone(4.0))
        assert ws.receive_json()["reasons"] == ["Secrecy"]
        send_audio(ws, tone(1.0))                      # partial buffer, discarded by clear
        ws.send_text(json.dumps({"type": "clear"}))
        assert ws.receive_json() == {"type": "cleared"}
        send_audio(ws, tone(4.0))
        msg = ws.receive_json()
    assert msg["reasons"] == ["Sensitive info request"]    # the earlier cue is gone
    assert msg["verdict"]["reasons"] == ["Sensitive info request"] and msg["verdict"]["windows"] == 1
    assert msg["timestamp"] == 9.0 and msg["window_index"] == 1


def test_frame_too_large_closes_session(make_client):
    client, _, _ = make_client(max_frame_bytes=2048)
    with client.websocket_connect("/ws/big") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00" * 4096)
        err = ws.receive_json()
        assert err["code"] == "frame_too_large" and err["fatal"] is True
        assert expect_close(ws) == 1009


def test_idle_session_times_out(make_client):
    client, _, _ = make_client(idle_timeout_seconds=0.3)
    with client.websocket_connect("/ws/idle") as ws:
        ws.receive_json()
        err = ws.receive_json()
        assert err["code"] == "idle_timeout" and err["fatal"] is True
        assert expect_close(ws) == 4408


def test_backlog_drops_oldest_windows_and_warns(make_client):
    client, _, _ = make_client(detector=FakeDetector(delay=0.4), max_backlog_windows=1)
    with client.websocket_connect("/ws/slow") as ws:
        ws.receive_json()
        send_audio(ws, tone(16.0))                      # 4 windows, much faster than real time
        ws.send_text(json.dumps({"type": "stop"}))
        messages = []
        while not messages or messages[-1]["type"] != "stopped":
            messages.append(ws.receive_json())
    results = [m for m in messages if m["type"] == "result"]
    warnings = [m for m in messages if m["type"] == "warning"]
    stopped = messages[-1]
    assert stopped["dropped_windows"] >= 1 and warnings
    assert all(w["code"] == "backlog_dropped" for w in warnings)
    assert len(results) + stopped["dropped_windows"] == 4
    assert [r["window_index"] for r in results] == sorted(r["window_index"] for r in results)


def test_silence_is_insufficient_audio(make_client):
    client, _, det = make_client()
    with client.websocket_connect("/ws/quiet") as ws:
        ws.receive_json()
        send_audio(ws, np.zeros(64000, np.float32))
        msg = ws.receive_json()
    assert msg["voice_status"] == msg["voice_flag"] == "insufficient_audio"
    assert msg["voice_score"] is None and msg["transcript_status"] == "no_speech"
    assert det.calls == 0


def test_clipped_audio_is_flagged(make_client):
    client, _, det = make_client()
    with client.websocket_connect("/ws/loud") as ws:
        ws.receive_json()
        send_audio(ws, np.sign(tone(4.0)).astype(np.float32))
        msg = ws.receive_json()
    assert msg["voice_status"] == msg["voice_flag"] == "clipped" and msg["voice_score"] is None
    assert det.calls == 0


def test_elevated_voice_score(make_client):
    client, _, _ = make_client(detector=FakeDetector(value=0.93))
    with client.websocket_connect("/ws/fake-voice") as ws:
        ws.receive_json()
        send_audio(ws, tone(4.0))
        msg = ws.receive_json()
    assert msg["voice_score"] == 0.93 and msg["voice_flag"] == "elevated" and msg["risk"] == "low"


def test_live_verdict_becomes_ai_voice_after_two_voice_windows(make_client):
    client, _, _ = make_client(texts=["hello how are you", "see you on sunday"], detector=FakeDetector(value=0.93))
    with client.websocket_connect("/ws/ai") as ws:
        ws.receive_json()
        send_audio(ws, tone(8.0))
        first, second = ws.receive_json(), ws.receive_json()
        ws.send_text(json.dumps({"type": "stop"}))
        stopped = ws.receive_json()
    assert first["verdict"]["verdict"] == "safe"            # one high window is not enough yet
    assert "No signs" not in first["verdict"]["message"]
    assert second["verdict"]["verdict"] == "ai_voice" and second["verdict"]["ai_voice"] is True
    assert stopped["verdict"]["title"] == "AI-generated voice, likely not a scam"
    assert stopped["verdict"]["voice_score"] == 1.0                    # every assessed window was flagged
    assert stopped["verdict"]["voice_flagged"] == stopped["verdict"]["voice_windows"] == 2


def test_pause_ends_a_window_early(make_client):
    client, _, _ = make_client()
    audio = tone(8.0)
    audio[int(3.4 * 16000):int(3.6 * 16000)] = 0.0         # a 200 ms pause inside the first window
    with client.websocket_connect("/ws/pause") as ws:
        ws.receive_json()
        send_audio(ws, audio)
        first = ws.receive_json()
    assert 3.4 < first["timestamp"] < 3.6 and first["duration"] == first["timestamp"]


def test_models_unavailable_are_reported_not_hidden(make_client):
    client, _, _ = make_client(transcriber=FakeTranscriber(available=False), detector=FakeDetector(value=None))
    with client.websocket_connect("/ws/degraded") as ws:
        ready = ws.receive_json()
        assert ready["models"] == {"transcription": "error", "anti_spoof": "error"}
        send_audio(ws, tone(4.0))
        msg = ws.receive_json()
    assert msg["transcript_status"] == "unavailable"
    assert msg["voice_status"] == msg["voice_flag"] == "unavailable" and msg["voice_score"] is None
    assert msg["verdict"]["verdict"] == "inconclusive"      # never "not a scam" when nothing was read
    assert "Speech recognition is unavailable" in msg["verdict"]["message"]


def test_analysis_error_is_reported_and_session_continues(make_client):
    client, _, _ = make_client(transcriber=FakeTranscriber(error=RuntimeError("boom")))
    with client.websocket_connect("/ws/err") as ws:
        ws.receive_json()
        send_audio(ws, tone(4.0))
        err = ws.receive_json()
        assert err == {"type": "error", "code": "analysis_failed", "message": err["message"], "fatal": False}
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}


def test_unknown_websocket_path_is_refused(make_client, tmp_path):
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.html").write_text("<h1>ui</h1>")
    client, _, _ = make_client(frontend_dir=tmp_path / "ui")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/nope") as ws:
            ws.receive_json()
