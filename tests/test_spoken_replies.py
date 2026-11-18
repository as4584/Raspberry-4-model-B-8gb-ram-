"""#38: the phone path speaks Reply.speech; screen text (Reply.text) is unchanged."""
import base64
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from receptionist.audio import MockASR
from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue
from receptionist.server import Settings, create_app

pytestmark = pytest.mark.timeout(60)
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=ZoneInfo("America/New_York"))
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")


@pytest.fixture
def d(tmp_path):
    return Dialogue(BookingStore(str(tmp_path / "r.sqlite")), clock=lambda: NOW)


def test_confirm_prompt_speech(d):
    r = d.handle("book december tenth at two pm", "s")
    assert r.text == "Please confirm: Thursday, December 10 at 02:00 PM for 30 minutes. Say yes or no."
    # 'Feel human': a short cached opener plays instantly while the rest synthesises.
    assert r.speech == "Okay, let me check that. Thursday, December tenth at two p m. For thirty minutes. Shall I book it?"


def test_booked_speech_has_no_uuid(d):
    d.handle("book december tenth at two pm", "s")
    r = d.handle("yes", "s")
    assert UUID.search(r.text)                          # screen still shows the reference
    assert r.speech == "Great, you're all set. Thursday, December tenth at two p m. Thank you for calling."
    assert not UUID.search(r.speech)


def test_help_speech_has_no_iso_date(d):
    r = d.handle("hello", "s")
    assert r.speech == "Tell me the day and time you'd like, for example, December tenth at two p m."
    assert not re.search(r"\d{4}-\d\d-\d\d", r.speech)


def test_replies_without_speech_fall_back_to_text(d):
    r = d.handle("I want a human", "s")
    assert r.speech is None and r.text


def test_phone_path_never_sends_a_uuid_to_tts(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEPTIONIST_DB", str(tmp_path / "w.sqlite"))
    spoken = []

    class RecTTS:
        async def synthesize(self, text):
            spoken.append(text)
            return bytes([0x10] * 320)

    app = create_app(Settings(dev_allow_unsigned=True, wss_url="wss://localhost/twilio/media"),
                     asr_factory=lambda: MockASR(["book 2027-12-10 14:00", "yes"]), tts=RecTTS())
    with TestClient(app) as c, c.websocket_connect("/twilio/media") as ws:
        ws.send_json({"event": "start", "streamSid": "M1", "start": {"callSid": "C1"}})
        for _ in range(2):
            for _ in range(5):
                ws.send_json({"event": "media", "streamSid": "M1", "media": {"payload": base64.b64encode(bytes([0] * 160)).decode()}})
            for _ in range(16):
                ws.send_json({"event": "media", "streamSid": "M1", "media": {"payload": base64.b64encode(bytes([255] * 160)).decode()}})
            while ws.receive_json()["event"] != "mark":
                pass
    joined = " ".join(spoken)
    assert "You're booked for Friday, December tenth at two p m." in joined
    assert not UUID.search(joined) and not re.search(r"\d{4}-\d\d-\d\d", joined)


def test_every_spoken_reply_opens_with_a_short_cacheable_sentence(d):
    from receptionist.dialogue import OPENERS
    from receptionist.server import split_sentences
    first = d.handle("book december tenth at two pm", "s")
    booked = d.handle("yes", "s")
    for r in (first, booked):
        opener = split_sentences(r.speech)[0]
        assert opener in OPENERS and len(opener) <= 40 and not any(c.isdigit() for c in opener)


def test_shall_i_book_it_still_accepts_yes_and_no(d):
    d.handle("book december tenth at two pm", "s")
    assert d.handle("no", "s").action != "booked" and d.store.active() == []
