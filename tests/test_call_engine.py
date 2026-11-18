"""T1: the call engine is provider-independent. It speaks only CallTransport, so the
same VAD/ASR/dialogue/TTS/barge-in logic runs behind Twilio, Asterisk or a test fake."""
import asyncio

from receptionist.audio import MockASR, MockTTS
from receptionist.booking import BookingStore
from receptionist.telephony import AudioIn, CallEngine, CallStart

LOUD, QUIET = b"\x00" * 160, b"\xff" * 160


class FakeTransport:
    def __init__(self, script):
        self.script, self.sent = script, []

    async def events(self):
        for item in self.script:
            if isinstance(item, float):
                await asyncio.sleep(item)
            else:
                yield item

    async def send_frame(self, frame): self.sent.append(("audio", len(frame)))
    async def flush(self): self.sent.append(("flush",))
    async def mark(self, name): self.sent.append(("mark", name))
    async def hangup(self): self.sent.append(("hangup",))
    async def transfer(self, target): self.sent.append(("transfer", target))


def speech(n_loud=10, n_quiet=20):
    return [AudioIn(LOUD)] * n_loud + [AudioIn(QUIET)] * n_quiet


def run(tmp_path, script, transcripts):
    engine = CallEngine(BookingStore(str(tmp_path / "e.sqlite")), asr=MockASR(transcripts), tts=MockTTS())
    t = FakeTransport(script)
    asyncio.run(engine.run(t))
    return t, engine


def test_one_turn_flushes_speaks_then_marks(tmp_path):
    t, _ = run(tmp_path, [CallStart("C1", "+15550000001"), *speech(), 0.5], ["book 2027-12-10 14:00"])
    kinds = [s[0] for s in t.sent]
    assert kinds[0] == "flush" and "audio" in kinds and t.sent[-1] == ("mark", "answer-1")


def test_caller_number_reaches_the_dialogue(tmp_path):
    t, engine = run(tmp_path, [CallStart("C1", "+15550000001"), *speech(), 0.5, *speech(), 0.5],
                    ["book 2027-12-10 14:00", "yes"])
    (row,) = engine.store.active()
    assert row["caller_id"] == "+15550000001"


def test_no_audio_before_start_is_ignored(tmp_path):
    t, _ = run(tmp_path, [*speech(), 0.3], ["book 2027-12-10 14:00"])
    assert not any(s[0] == "audio" for s in t.sent)
