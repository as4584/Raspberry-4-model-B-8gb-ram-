"""(Uses the two-sentence spoken confirm prompt; the help reply became one spoken sentence in #38.)
S4/S6: replies are synthesised and streamed sentence by sentence, so the
caller hears sentence 1 while later sentences are still being synthesised.
PI4 2026-11-18: Piper rendered the whole 8 s help reply before the first
frame went out (11-21 s end-of-speech -> first audio, throttled)."""
import asyncio
import base64
import time

import pytest
from fastapi.testclient import TestClient

from receptionist import server
from receptionist.audio import MockASR
from receptionist.server import Settings, create_app

pytestmark = pytest.mark.timeout(60)
SLOW_S = 1.5


class SentenceTTS:
    """2 frames per call; any text that contains the 2nd help sentence is slow."""
    def __init__(self):
        self.calls = []

    async def synthesize(self, text):
        self.calls.append(text)
        if text == "Want me to book it?":
            await asyncio.sleep(SLOW_S)
        return bytes([0x10] * 320)


def run_call(tts):
    app = create_app(Settings(dev_allow_unsigned=True,
                              wss_url="wss://localhost/twilio/media"),
                     asr_factory=lambda: MockASR(["book 2027-12-10 14:00"]), tts=tts)
    with TestClient(app) as c, c.websocket_connect("/twilio/media") as ws:
        ws.send_json({"event": "start", "streamSid": "M1", "start": {"callSid": "C1"}})
        for _ in range(5):
            ws.send_json({"event": "media", "streamSid": "M1", "media": {"payload": base64.b64encode(bytes([0] * 160)).decode()}})
        for _ in range(16):
            ws.send_json({"event": "media", "streamSid": "M1", "media": {"payload": base64.b64encode(bytes([255] * 160)).decode()}})
        t0 = time.monotonic()
        assert ws.receive_json()["event"] == "clear"
        first = ws.receive_json()
        first_s = time.monotonic() - t0
        events = [first["event"]]
        while events[-1] != "mark":
            events.append(ws.receive_json()["event"])
    return first_s, events


def test_first_sentence_streams_before_slow_second_sentence_is_synthesised(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEPTIONIST_DB", str(tmp_path / "s.sqlite"))
    tts = SentenceTTS()
    first_s, events = run_call(tts)
    assert first_s < SLOW_S / 2, f"first audio after {first_s:.2f}s: reply not streamed per sentence"
    # The confirm prompt is spoken as three sentences, opener first (feel-human wording).
    assert tts.calls == ["Okay, let me check that.", "Friday, December tenth at two p m is open.",
                         "Want me to book it?"]
    assert events.count("media") == 6 and events[-1] == "mark"


@pytest.mark.parametrize("text,expect", [
    ("One. Two? Three!", ["One.", "Two?", "Three!"]),
    ("No punctuation", ["No punctuation"]),
    ("Confirmed for 2027-12-10 02:00 PM. Your reference is 1.2.3.", ["Confirmed for 2027-12-10 02:00 PM.", "Your reference is 1.2.3."]),
    ("  ", []),
])
def test_split_sentences(text, expect):
    assert server.split_sentences(text) == expect
