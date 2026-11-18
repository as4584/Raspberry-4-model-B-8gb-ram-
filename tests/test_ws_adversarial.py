"""S5 adversarial tests for the Twilio-style media WebSocket. Untrusted input
must be refused with a policy close (1008), never crash the handler (1011) or
leak into another call. Simulator evidence only; not live Twilio."""
import base64

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from receptionist.audio import MockASR, MockTTS
from receptionist.server import Settings, create_app

# A regression to "never closes" must fail fast, not stall CI.
pytestmark = pytest.mark.timeout(15)


def make_client(tmp_path, asr=None):
    app = create_app(Settings(db_path=str(tmp_path / "ws.sqlite"), dev_allow_unsigned=True,
                              wss_url="wss://localhost/twilio/media"),
                     asr_factory=asr or (lambda: MockASR(["book 2027-12-10 14:00", "yes"])),
                     tts=MockTTS())
    return TestClient(app)


def start(sid="M1", call="C1"):
    return {"event": "start", "streamSid": sid, "start": {"callSid": call}}


def media(payload: bytes, sid="M1"):
    return {"event": "media", "streamSid": sid, "media": {"payload": base64.b64encode(payload).decode()}}


def closed_with(ws, send):
    send(ws)
    with pytest.raises(WebSocketDisconnect) as e:
        ws.receive_json()
    return e.value.code


@pytest.mark.parametrize("frame", [[1, 2], "hello", 42, None, True])
def test_non_object_json_is_policy_close(tmp_path, frame):
    with make_client(tmp_path) as c, c.websocket_connect("/twilio/media") as ws:
        assert closed_with(ws, lambda w: w.send_json(frame)) == 1008


@pytest.mark.parametrize("bad_start", [{"event": "start", "streamSid": "M1", "start": "x"},
                                       {"event": "start", "streamSid": "M1", "start": [1]},
                                       {"event": "start", "streamSid": 5, "start": {"callSid": "C1"}}])
def test_malformed_start_is_policy_close(tmp_path, bad_start):
    with make_client(tmp_path) as c, c.websocket_connect("/twilio/media") as ws:
        assert closed_with(ws, lambda w: w.send_json(bad_start)) == 1008


@pytest.mark.parametrize("bad_media", ["not-a-dict", [1], 7])
def test_malformed_media_field_is_policy_close(tmp_path, bad_media):
    with make_client(tmp_path) as c, c.websocket_connect("/twilio/media") as ws:
        ws.send_json(start())
        assert closed_with(ws, lambda w: w.send_json({"event": "media", "streamSid": "M1", "media": bad_media})) == 1008


def test_invalid_utf8_or_non_json_text_is_policy_close(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/twilio/media") as ws:
        assert closed_with(ws, lambda w: w.send_text("{not json")) == 1008


def test_media_before_start_is_policy_close(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/twilio/media") as ws:
        assert closed_with(ws, lambda w: w.send_json(media(bytes(160)))) == 1008


def test_spoofed_stream_sid_is_policy_close(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/twilio/media") as ws:
        ws.send_json(start("M1"))
        assert closed_with(ws, lambda w: w.send_json(media(bytes(160), sid="M2"))) == 1008


def test_second_start_on_same_socket_is_policy_close(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/twilio/media") as ws:
        ws.send_json(start("M1", "C1"))
        assert closed_with(ws, lambda w: w.send_json(start("M2", "C2"))) == 1008


def test_oversized_payload_closes_1009(tmp_path):
    with make_client(tmp_path) as c, c.websocket_connect("/twilio/media") as ws:
        ws.send_json(start())
        big = {"event": "media", "streamSid": "M1", "media": {"payload": "A" * 20004}}
        assert closed_with(ws, lambda w: w.send_json(big)) == 1009


def test_two_callers_on_separate_sockets_never_cross(tmp_path):
    """Each socket's outbound frames carry only its own streamSid, and each
    call's booking flow is isolated even though both share one store."""
    seen = {"M1": set(), "M2": set()}
    with make_client(tmp_path) as c:
        with c.websocket_connect("/twilio/media") as a, c.websocket_connect("/twilio/media") as b:
            a.send_json(start("M1", "C1"))
            b.send_json(start("M2", "C2"))
            for ws, sid in ((a, "M1"), (b, "M2")):
                for _ in range(5):
                    ws.send_json(media(bytes([0] * 160), sid))        # loud
                for _ in range(16):
                    ws.send_json(media(bytes([255] * 160), sid))      # silence -> endpoint
            for ws, sid in ((a, "M1"), (b, "M2")):
                for _ in range(4):                                     # clear, media, media, mark
                    seen[sid].add(ws.receive_json()["streamSid"])
    assert seen == {"M1": {"M1"}, "M2": {"M2"}}
