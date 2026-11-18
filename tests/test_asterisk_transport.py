"""T3: Asterisk chan_websocket transport (Asterisk 22.12, control_message_format=json).
Asterisk connects to /asterisk/media; binary frames are raw 8 kHz u-law, text frames
are JSON events; we answer with binary audio and JSON commands. A fake peer stands in
for Asterisk here; SIPp + real Asterisk on the Pi is T4."""
import json

import pytest
from fastapi.testclient import TestClient

from receptionist.audio import MockASR, MockTTS
from receptionist.server import Settings, create_app

pytestmark = pytest.mark.timeout(20)
LOUD, QUIET = b"\x00" * 160, b"\xff" * 160
START = {"event": "MEDIA_START", "connection_id": "c1", "channel": "WebSocket/c1-0001",
         "channel_id": "1763500000.1", "format": "ulaw", "optimal_frame_size": 160, "ptime": 20}


def client(tmp_path, transcripts=("book 2027-12-10 14:00",)):
    app = create_app(Settings(db_path=str(tmp_path / "a.sqlite"), dev_allow_unsigned=False),
                     asr_factory=lambda: MockASR(list(transcripts)), tts=MockTTS())
    return TestClient(app)


def speak(ws, loud=10, quiet=20):
    for _ in range(loud):
        ws.send_bytes(LOUD)
    for _ in range(quiet):
        ws.send_bytes(QUIET)


def receive_until_mark(ws):
    got = []
    while True:
        msg = ws.receive()
        if msg.get("bytes") is not None:
            got.append(("audio", len(msg["bytes"])))
        else:
            cmd = json.loads(msg["text"])
            got.append((cmd["command"], cmd.get("correlation_id")))
            if cmd["command"] == "MARK_MEDIA":
                return got


def test_turn_flushes_streams_binary_audio_then_marks(tmp_path):
    with client(tmp_path) as c, c.websocket_connect("/asterisk/media?caller=%2B15550000001") as ws:
        ws.send_text(json.dumps(START))
        speak(ws)
        got = receive_until_mark(ws)
    assert got[0] == ("FLUSH_MEDIA", None)
    assert ("audio", 160) in got and got[-1] == ("MARK_MEDIA", "answer-1")


def test_caller_number_comes_from_dialplan_uri_param(tmp_path):
    with client(tmp_path, ["book 2027-12-10 14:00", "yes"]) as c, \
            c.websocket_connect("/asterisk/media?caller=%2B15550000001") as ws:
        ws.send_text(json.dumps(START))
        speak(ws); receive_until_mark(ws)
        speak(ws); receive_until_mark(ws)
        rows = c.app.state.store.active()
    assert [r["caller_id"] for r in rows] == ["+15550000001"]


def test_audio_waits_while_asterisk_says_xoff(tmp_path):
    with client(tmp_path) as c, c.websocket_connect("/asterisk/media?caller=%2B15550000001") as ws:
        ws.send_text(json.dumps(START))
        ws.send_text(json.dumps({"event": "MEDIA_XOFF", "channel_id": START["channel_id"]}))
        speak(ws)
        first = json.loads(ws.receive()["text"])
        assert first["command"] == "FLUSH_MEDIA"
        ws.send_text(json.dumps({"event": "MEDIA_XON", "channel_id": START["channel_id"]}))
        got = receive_until_mark(ws)
    assert ("audio", 160) in got


@pytest.mark.parametrize("bad", ["not json", json.dumps([1, 2]), json.dumps({"event": "MEDIA_START"})])
def test_malformed_control_is_policy_close(tmp_path, bad):
    with client(tmp_path) as c, c.websocket_connect("/asterisk/media") as ws:
        ws.send_text(bad)
        msg = ws.receive()
    assert msg["type"] == "websocket.close" and msg["code"] == 1008


def test_oversized_binary_frame_is_refused(tmp_path):
    with client(tmp_path) as c, c.websocket_connect("/asterisk/media") as ws:
        ws.send_text(json.dumps(START))
        ws.send_bytes(b"\x00" * 20000)
        msg = ws.receive()
    assert msg["type"] == "websocket.close" and msg["code"] == 1009


def test_bad_caller_param_is_treated_as_anonymous(tmp_path):
    with client(tmp_path, ["book 2027-12-10 14:00", "yes"]) as c, \
            c.websocket_connect("/asterisk/media?caller=evil%27--") as ws:
        ws.send_text(json.dumps(START))
        speak(ws); receive_until_mark(ws)
        speak(ws); receive_until_mark(ws)
        rows = c.app.state.store.active()
    assert all(r["caller_id"] is None for r in rows)
