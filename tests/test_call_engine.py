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


def test_turn_ends_when_audio_stops_arriving_silence_suppression(tmp_path):
    # Pi SIPp run: after the caller's sentence no more RTP arrived (silence suppression /
    # DTX does this on real networks too), so the 300 ms quiet-frame count never completed
    # and the turn stayed open until the next words 7 s later. Wall-clock silence ends it.
    asr = RecordingASR()
    engine = CallEngine(BookingStore(str(tmp_path / "x.sqlite")), asr=asr, tts=MockTTS())
    script = [CallStart("C1", None), *[AudioIn(LOUD)] * 10, *[AudioIn(QUIET)] * 3, 0.8]
    asyncio.run(engine.run(FakeTransport(script)))
    assert len(asr.packets) == 1


def test_logs_end_of_speech_to_first_reply_audio(tmp_path, caplog):
    # Real-call latency without recording the caller: time from the last voiced frame to
    # the first reply frame handed to the transport.
    import logging, re
    with caplog.at_level(logging.INFO, logger="receptionist.call"):
        # Caller speaks, then goes quiet with no packets (real time passes): the turn ends
        # on the 300 ms silence watchdog, so latency includes that wait.
        run(tmp_path, [CallStart("C1", "+15550000001"), *[AudioIn(LOUD)] * 10, 0.9], ["book 2027-12-10 14:00"])
    m = re.search(r"phase=reply_latency end_of_speech_to_audio_s=(\d+\.\d+)", caplog.text)
    assert m and 0.25 <= float(m.group(1)) < 2.0          # includes the 300 ms end-of-turn wait


def test_line_noise_does_not_count_as_speech(tmp_path):
    # Real call: steady line noise above the fixed loudness trigger kept the turn open
    # (a 1-word turn reached whisper as 3.5 s). Speech must stand out from the call's noise.
    noise = b"\xb0" * 160                       # steady hum, louder than the old fixed trigger
    asr = RecordingASR()
    engine = CallEngine(BookingStore(str(tmp_path / "n.sqlite")), asr=asr, tts=MockTTS(), greet=True)
    script = [CallStart("C1", None), *[AudioIn(noise)] * 50,          # 1 s of noise: learn the floor
              *[AudioIn(LOUD)] * 10, *[AudioIn(noise)] * 40, 0.5]     # 0.2 s speech, then noise
    asyncio.run(engine.run(FakeTransport(script)))
    assert len(asr.packets) == 1
    assert len(asr.packets[0]) <= (10 + 15 + 15) * 160               # speech + pre-roll + 300 ms


def test_noise_alone_never_starts_a_turn(tmp_path):
    from receptionist.audio import rms_mulaw
    noise = b"\xb0" * 160
    assert rms_mulaw(noise) > 650                                    # would have triggered before
    asr = RecordingASR()
    engine = CallEngine(BookingStore(str(tmp_path / "m.sqlite")), asr=asr, tts=MockTTS(), greet=True)
    asyncio.run(engine.run(FakeTransport([CallStart("C1", None), *[AudioIn(noise)] * 150, 0.5])))
    assert asr.packets == []


class SlowDialogue:
    def __init__(self, delay): self.delay = delay
    def handle(self, text, session, caller_id=None):
        import time as _t
        from receptionist.dialogue import Reply
        _t.sleep(self.delay)
        return Reply("Great, you're all set.", "booked", speech="Great, you're all set.")


def run_with_dialogue(tmp_path, dialogue):
    spoken = []
    class TTS:
        async def synthesize(self, text):
            spoken.append(text); return b"\xff" * 160
    eng = CallEngine(BookingStore(str(tmp_path / "s.sqlite")), asr=MockASR(["yes"]), tts=TTS(),
                     dialogue_factory=lambda: dialogue)
    asyncio.run(eng.run(FakeTransport([CallStart("C1", None), *speech(), 1.6])))
    return spoken


def test_slow_dialogue_is_masked_with_one_moment(tmp_path):
    # Pi: bookings occasionally took 2-5 s (slow USB fsync); a caller hears dead air.
    spoken = run_with_dialogue(tmp_path, SlowDialogue(0.9))
    assert spoken[:2] == ["One moment.", "Great, you're all set."]


def test_fast_dialogue_gets_no_filler(tmp_path):
    assert run_with_dialogue(tmp_path, SlowDialogue(0.0)) == ["Great, you're all set."]
