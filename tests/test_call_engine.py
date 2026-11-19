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


def test_service_emits_per_phase_call_timings():
    # Under uvicorn nothing configured receptionist.call, so the per-phase latency
    # lines (asr took_s, first_audio_ready) never reached the journal on the Pi.
    import logging
    from receptionist.server import create_app
    create_app()
    lg = logging.getLogger("receptionist.call")
    assert lg.isEnabledFor(logging.INFO) and lg.handlers


def test_transcripts_are_not_logged_by_default(tmp_path, caplog, monkeypatch):
    import logging
    monkeypatch.delenv("LOG_TRANSCRIPTS", raising=False)
    with caplog.at_level(logging.INFO, logger="receptionist.call"):
        run(tmp_path, [CallStart("C1", "+15550000001"), *speech(), 0.5], ["book 2027-12-10 14:00"])
    assert "2027-12-10" not in caplog.text and "heard=" not in caplog.text


class RecordingASR:
    def __init__(self): self.packets = []
    async def transcribe(self, b): self.packets.append(b); return ""


def test_soft_onset_before_the_vad_trigger_is_kept(tmp_path):
    # Pi, 3 concurrent SIPp calls: whisper heard "December 10th at 10 am." -- the soft
    # start of "Book" fell below the loudness trigger and was dropped. Keep 300 ms pre-roll.
    asr = RecordingASR()
    engine = CallEngine(BookingStore(str(tmp_path / "p.sqlite")), asr=asr, tts=MockTTS())
    soft = b"\xf0" * 160                                   # quiet but not silent
    script = [CallStart("C1", None), *[AudioIn(QUIET)] * 20, *[AudioIn(soft)] * 10, *speech(), 0.5]
    asyncio.run(engine.run(FakeTransport(script)))
    (packet,) = asr.packets
    assert packet.startswith(soft * 10) or soft * 10 in packet[:15 * 160]


def test_greets_the_caller_when_the_call_starts(tmp_path):
    from receptionist.dialogue import GREETING, OPENERS
    engine = CallEngine(BookingStore(str(tmp_path / "g.sqlite")), asr=MockASR([]), tts=MockTTS(), greet=True)
    t = FakeTransport([CallStart("C1", "+15550000001"), 0.3])
    asyncio.run(engine.run(t))
    kinds = [s[0] for s in t.sent]
    assert "audio" in kinds and t.sent[-1] == ("mark", "greeting")
    assert len(GREETING) <= 60 and not any(c.isdigit() for c in GREETING)


class SlowFirstASR:
    """First transcription is slow (still running when the caller speaks again)."""
    def __init__(self):
        self.packets = []

    async def transcribe(self, b):
        self.packets.append(len(b))
        if len(self.packets) == 1:
            await asyncio.sleep(1.0)
        return "book 2027-12-10 14:00" if len(self.packets) > 1 else ""


def test_speech_before_any_reply_is_carried_into_the_next_utterance(tmp_path):
    # Pi barge-in run: the caller said "yes" while turn 1 was still being transcribed;
    # turn 1 was cancelled and lost, so "yes" had nothing to confirm. Unanswered speech
    # must be kept and heard together with what follows.
    asr = SlowFirstASR()
    engine = CallEngine(BookingStore(str(tmp_path / "c.sqlite")), asr=asr, tts=MockTTS())
    first, second = speech(), speech()
    asyncio.run(engine.run(FakeTransport([CallStart("C1", None), *first, 0.2, *second, 1.5])))
    assert len(asr.packets) == 2
    assert asr.packets[1] >= asr.packets[0] + 10 * 160          # carried first utterance + new one


def test_speech_after_a_reply_started_is_not_carried(tmp_path):
    # Once the receptionist has answered, a new utterance is a new turn (true barge-in).
    class Answering(RecordingASR):
        async def transcribe(self, b):
            self.packets.append(b); return "hello"
    asr = Answering()
    engine = CallEngine(BookingStore(str(tmp_path / "d.sqlite")), asr=asr, tts=MockTTS())
    asyncio.run(engine.run(FakeTransport([CallStart("C1", None), *speech(), 0.6, *speech(), 0.6])))
    assert len(asr.packets) == 2 and abs(len(asr.packets[0]) - len(asr.packets[1])) <= 2400   # at most the pre-roll, never a carried turn
