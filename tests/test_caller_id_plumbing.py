"""S7 plumbing: Twilio's caller number (From) reaches the Dialogue so caller
verification works on real calls. /twilio/voice embeds it as a <Parameter> on
the <Stream>; Twilio returns it in start.customParameters. Simulator evidence."""
import base64
import configparser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from receptionist.audio import MockASR, MockTTS
from receptionist.booking import BookingStore
from receptionist.server import Settings, create_app

pytestmark = pytest.mark.timeout(30)
ROOT = Path(__file__).resolve().parents[1]


def app_for(tmp_path, scripts=(["book 2027-12-10 14:00", "yes"],), require=None):
    it = iter(scripts)
    s = Settings(db_path=str(tmp_path / "c.sqlite"), dev_allow_unsigned=True, wss_url="wss://localhost/twilio/media")
    if require is not None:
        s.require_caller_id = require
    return create_app(s, asr_factory=lambda: MockASR(next(it)), tts=MockTTS())


@pytest.mark.parametrize("frm", ["+15551234567", "15551234567", "+447911123456"])
def test_voice_webhook_embeds_valid_caller_number(tmp_path, frm):
    xml = TestClient(app_for(tmp_path)).post("/twilio/voice", data={"CallSid": "CA1", "From": frm}).text
    assert f'<Parameter name="callerId" value="{frm}" />' in xml
    assert xml.index("<Stream") < xml.index("<Parameter") < xml.index("</Stream>")


@pytest.mark.parametrize("frm", ["", "anonymous", "Anonymous", "+266696687x", '"/><Hangup/><x a="', "12", "+" + "1" * 20])
def test_voice_webhook_omits_missing_or_malformed_number(tmp_path, frm):
    xml = TestClient(app_for(tmp_path)).post("/twilio/voice", data={"CallSid": "CA1", "From": frm}).text
    assert "<Parameter" not in xml and "<Hangup" not in xml
    assert "<Connect><Stream" in xml


def _speak(ws, sid):
    for _ in range(5):
        ws.send_json({"event": "media", "streamSid": sid, "media": {"payload": base64.b64encode(bytes([0] * 160)).decode()}})
    for _ in range(16):
        ws.send_json({"event": "media", "streamSid": sid, "media": {"payload": base64.b64encode(bytes([255] * 160)).decode()}})
    out = [ws.receive_json()]
    while out[-1]["event"] != "mark":          # replies stream per sentence
        out.append(ws.receive_json())
    return out


def _call(tmp_path, params, require=None):
    app = app_for(tmp_path, require=require)
    with TestClient(app) as c, c.websocket_connect("/twilio/media") as ws:
        start = {"callSid": "CA1"}
        if params is not None:
            start["customParameters"] = params
        ws.send_json({"event": "start", "streamSid": "M1", "start": start})
        _speak(ws, "M1")
        _speak(ws, "M1")
    return BookingStore(str(tmp_path / "c.sqlite")).active()


def test_caller_number_from_stream_is_stored_on_booking(tmp_path):
    rows = _call(tmp_path, {"callerId": "+15551234567"})
    assert len(rows) == 1 and rows[0]["caller_id"] == "+15551234567"


@pytest.mark.parametrize("params", [None, {}, {"callerId": "nope"}, {"callerId": 5551234567}, "x"])
def test_missing_or_malformed_stream_caller_is_anonymous(tmp_path, params):
    rows = _call(tmp_path, params)
    assert len(rows) == 1 and rows[0]["caller_id"] is None


def test_require_caller_id_refuses_anonymous_booking_on_call(tmp_path):
    assert _call(tmp_path, None, require=True) == []
    assert len(_call(tmp_path / "b", {"callerId": "+15551234567"}, require=True)) == 1


def test_require_caller_id_from_env(monkeypatch):
    monkeypatch.setenv("REQUIRE_CALLER_ID", "1")
    assert Settings().require_caller_id is True
    monkeypatch.setenv("REQUIRE_CALLER_ID", "0")
    assert Settings().require_caller_id is False


def test_production_unit_requires_caller_id():
    unit = (ROOT / "deploy" / "pi-receptionist.service").read_text()
    assert "Environment=REQUIRE_CALLER_ID=1" in unit
