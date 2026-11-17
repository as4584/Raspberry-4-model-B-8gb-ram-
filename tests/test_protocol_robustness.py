"""Protocol robustness tests — Ramona (Laptop 2).

Found during V0 StreamDouble compatibility analysis: server.py closes
the WebSocket with 1008 on any unrecognized event (including 'dtmf',
which StreamDouble legitimately sends). These tests pin the correct
behavior: unknown events should be silently ignored, not close the
connection.

TDD gate:
  RED — test_dtmf_event_does_not_close_connection fails before fix
  GREEN — after changing `else: ws.close(1008)` to `else: continue`
"""
import base64
from fastapi.testclient import TestClient
from receptionist.server import Settings, create_app
from receptionist.audio import MockASR, MockTTS


def make_app(tmp_path):
    return create_app(
        Settings(db_path=str(tmp_path / "test.sqlite"), dev_allow_unsigned=True,
                 wss_url="wss://localhost/twilio/media"),
        asr_factory=lambda: MockASR(["book 2027-12-10 14:00", "yes"]),
        tts=MockTTS(),
    )


def inbound(payload, sid):
    return {"event": "media", "streamSid": sid,
            "media": {"payload": base64.b64encode(payload).decode()}}


def test_dtmf_event_does_not_close_connection(tmp_path):
    """StreamDouble sends dtmf events; server must ignore them, not close.

    Protocol contract: unrecognized Twilio event types must be silently
    skipped so the connection stays alive for the rest of the call.
    """
    app = make_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            # Inject a dtmf event mid-call (StreamDouble can send these)
            ws.send_json({"event": "dtmf", "streamSid": "M1",
                          "dtmf": {"digit": "5", "finished": True}})
            # Send speech + silence to trigger a full round-trip after dtmf
            for _ in range(5):
                ws.send_json(inbound(bytes([0] * 160), "M1"))
            for _ in range(16):
                ws.send_json(inbound(bytes([255] * 160), "M1"))
            # If dtmf closed the connection, receive_json would raise.
            clear = ws.receive_json()
            assert clear["event"] == "clear" and clear["streamSid"] == "M1"
            media = ws.receive_json()
            assert media["event"] == "media"
            ws.receive_json()  # second media chunk
            mark = ws.receive_json()
            assert mark["event"] == "mark"
            ws.send_json({"event": "stop", "streamSid": "M1"})


def test_unknown_event_type_ignored(tmp_path):
    """Any unknown event type should be silently skipped, not close the connection."""
    app = make_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M2",
                          "start": {"callSid": "C2"}})
            ws.send_json({"event": "unknown_future_twilio_event",
                          "streamSid": "M2", "data": "ignored"})
            ws.send_json({"event": "stop", "streamSid": "M2"})
            # If unknown event closed connection, the stop would have failed.
            # Test passes as long as no exception is raised here.


def test_missing_streamSid_in_media_closes_cleanly(tmp_path):
    """Media events with wrong or missing streamSid should close cleanly."""
    app = make_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M3",
                          "start": {"callSid": "C3"}})
            # Send media with wrong streamSid
            ws.send_json(inbound(bytes([0] * 160), "WRONG_SID"))
            # Server closes; we expect connection to end without crash
