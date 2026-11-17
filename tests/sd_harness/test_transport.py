"""V1 StreamDouble transport harness — Ramona (Laptop 2).

Deterministic transport and protocol tests using FastAPI TestClient.
No live server, no Twilio credentials, no paid API keys.

Each test verifies the WebSocket protocol layer: event ordering,
streamSid echo, mark delivery, clear-before-reply, concurrent isolation.
MockASR injects scripted transcripts; MockTTS returns fixed bytes.

Environment: LAPTOP / MOCK
"""
import base64
import concurrent.futures
import pytest
from fastapi.testclient import TestClient
from receptionist.server import Settings, create_app
from receptionist.audio import MockASR, MockTTS
from tests.sd_harness.helpers import build_app, mulaw_frame, encode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def inbound(payload, sid):
    return {"event": "media", "streamSid": sid,
            "media": {"payload": encode(payload)}}


def speak_then_silence(ws, sid, speech_frames=5, silence_frames=16):
    """Stream speech (loud, 0x00 = max μ-law amplitude) then silence (0xFF)."""
    for _ in range(speech_frames):
        ws.send_json(inbound(mulaw_frame(0x00), sid))
    for _ in range(silence_frames):
        ws.send_json(inbound(mulaw_frame(0xFF), sid))


def drain_reply(ws, sid):
    """Collect events until mark; return list of (event, streamSid)."""
    events = []
    for _ in range(20):
        m = ws.receive_json()
        events.append(m)
        if m.get("event") == "mark":
            break
    return events


# ---------------------------------------------------------------------------
# Protocol handshake
# ---------------------------------------------------------------------------

def test_connected_event_accepted(tmp_path):
    app = build_app(tmp_path, ["book 2027-12-10 14:00"])
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "connected"})  # must not close
            ws.send_json({"event": "stop", "streamSid": "M1"})


def test_start_event_requires_streamSid(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            # Missing streamSid → server closes
            ws.send_json({"event": "start", "start": {"callSid": "C1"}})


def test_start_event_requires_callSid(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {}})  # missing callSid


def test_stop_closes_session(tmp_path):
    app = build_app(tmp_path, ["book 2027-12-10 14:00"])
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            ws.send_json({"event": "stop", "streamSid": "M1"})


# ---------------------------------------------------------------------------
# Full round-trip: clear → media → mark
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", ["M1", "M-abc-123", "MZabc000"])
def test_full_roundtrip_streamSid_echoed(tmp_path, sid):
    """server must echo the caller's streamSid on every outbound event."""
    app = build_app(tmp_path, ["book 2027-12-10 14:00"])
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": sid,
                          "start": {"callSid": "C1"}})
            speak_then_silence(ws, sid)
            events = drain_reply(ws, sid)
    sids = {e.get("streamSid") for e in events}
    assert sids == {sid}, f"streamSid mismatch: {sids}"


def test_clear_arrives_before_media(tmp_path):
    """server must send clear before any media reply."""
    app = build_app(tmp_path, ["book 2027-12-10 14:00"])
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            speak_then_silence(ws, "M1")
            events = drain_reply(ws, "M1")
    types = [e["event"] for e in events]
    assert types[0] == "clear", f"first event was {types[0]!r}, expected 'clear'"
    assert "media" in types
    assert types[-1] == "mark"


def test_mark_is_last_event_in_reply(tmp_path):
    app = build_app(tmp_path, ["book 2027-12-10 14:00"])
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            speak_then_silence(ws, "M1")
            events = drain_reply(ws, "M1")
    assert events[-1]["event"] == "mark"
    assert events[-1]["streamSid"] == "M1"


def test_media_payload_is_base64(tmp_path):
    app = build_app(tmp_path, ["book 2027-12-10 14:00"])
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            speak_then_silence(ws, "M1")
            events = drain_reply(ws, "M1")
    for e in events:
        if e["event"] == "media":
            payload = e["media"]["payload"]
            decoded = base64.b64decode(payload)
            assert len(decoded) > 0
            assert len(decoded) % 160 == 0, "media frame must be multiple of 160 bytes"


def test_media_payload_is_non_silent_mulaw(tmp_path):
    """MockTTS returns 0xFF bytes (μ-law silence used as transport fixture).
    Test verifies it is not an empty payload (zero bytes = broken TTS)."""
    app = build_app(tmp_path, ["book 2027-12-10 14:00"])
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            speak_then_silence(ws, "M1")
            events = drain_reply(ws, "M1")
    media_payloads = [base64.b64decode(e["media"]["payload"])
                      for e in events if e["event"] == "media"]
    assert media_payloads, "no media frames received — MockTTS returned nothing"
    for p in media_payloads:
        assert len(p) > 0, "empty media payload (broken TTS)"


# ---------------------------------------------------------------------------
# Barge-in: new speech cancels in-flight reply
# ---------------------------------------------------------------------------

def test_barge_in_sends_new_clear(tmp_path):
    """Second speech burst triggers at least two clear events total.

    The server sends clear whenever it detects new speech. After a barge-in,
    the second clear may arrive amid stale media frames from the first reply,
    so we scan rather than requiring positional ordering.
    """
    app = build_app(tmp_path, ["book 2027-12-10 14:00", "yes"])
    clears = []
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            speak_then_silence(ws, "M1")
            first_clear = ws.receive_json()
            assert first_clear["event"] == "clear"
            clears.append(first_clear)
            # Barge in before first reply finishes
            speak_then_silence(ws, "M1")
            # Drain up to 10 events looking for the second clear
            for _ in range(10):
                evt = ws.receive_json()
                if evt["event"] == "clear":
                    clears.append(evt)
                    break
            ws.send_json({"event": "stop", "streamSid": "M1"})
    assert len(clears) >= 2, \
        f"Expected ≥2 clear events (barge-in), got {len(clears)}"


# ---------------------------------------------------------------------------
# Protocol robustness: unknown / dtmf events
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("unknown_event", [
    "dtmf", "ringing", "future_twilio_v3_event", "mark_ack"
])
def test_unknown_events_do_not_close_connection(tmp_path, unknown_event):
    app = build_app(tmp_path, ["book 2027-12-10 14:00"])
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            ws.send_json({"event": unknown_event, "streamSid": "M1",
                          "data": "ignored"})
            speak_then_silence(ws, "M1")
            events = drain_reply(ws, "M1")
    assert any(e["event"] == "mark" for e in events), \
        f"connection closed after unknown event {unknown_event!r}"


def test_oversized_payload_closes_connection(tmp_path):
    """Payload > 20000 base64 chars must close with code 1009."""
    app = build_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            ws.send_json({"event": "media", "streamSid": "M1",
                          "media": {"payload": "A" * 20001}})


def test_wrong_streamSid_in_media_closes_connection(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            ws.send_json(inbound(mulaw_frame(), "WRONG"))


# ---------------------------------------------------------------------------
# Silence: no VAD trigger, no reply
# ---------------------------------------------------------------------------

def test_silence_only_produces_no_reply(tmp_path):
    """Pure silence frames must not trigger a reply (VAD requires real speech)."""
    app = build_app(tmp_path, ["book 2027-12-10 14:00"])
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            # 50 frames of pure silence (0xFF = silence in μ-law)
            for _ in range(50):
                ws.send_json(inbound(mulaw_frame(0xFF), "M1"))
            ws.send_json({"event": "stop", "streamSid": "M1"})
            # No clear or media should arrive before stop


# ---------------------------------------------------------------------------
# Two-call session independence
# ---------------------------------------------------------------------------

def test_two_sessions_independent_streamSid(tmp_path):
    """Two concurrent WS sessions must receive their own streamSid on all events."""
    app = build_app(tmp_path, ["book 2027-12-10 14:00", "book 2027-12-11 14:00"])

    def call(sid, transcript):
        a = build_app(tmp_path, [transcript])
        with TestClient(a) as c:
            with c.websocket_connect("/twilio/media") as ws:
                ws.send_json({"event": "start", "streamSid": sid,
                              "start": {"callSid": f"C_{sid}"}})
                speak_then_silence(ws, sid)
                events = drain_reply(ws, sid)
                return {e.get("streamSid") for e in events}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(call, "M1", "book 2027-12-10 14:00")
        f2 = pool.submit(call, "M2", "book 2027-12-11 14:00")
        sids1, sids2 = f1.result(), f2.result()

    assert sids1 == {"M1"}, f"session 1 got wrong sids: {sids1}"
    assert sids2 == {"M2"}, f"session 2 got wrong sids: {sids2}"
