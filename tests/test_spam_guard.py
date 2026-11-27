"""Spam guard: a robocall or a call going nowhere ends politely, so it can't burn minutes
or put junk on Alex's calendar. A real caller who gets misheard once is never cut off."""
import asyncio

import pytest

from receptionist.booking import BookingStore
from receptionist.brain import Brain
from receptionist.dialogue import Reply
from receptionist.telephony import AudioIn, CallEngine, CallStart
from tests.test_call_engine import LOUD, QUIET, FakeTransport
from tests.test_kindness import CALLER, NOW, FakeIntents, said
from receptionist.audio import MockASR


@pytest.fixture
def brain(tmp_path):
    return Brain(BookingStore(str(tmp_path / "s.sqlite")), intents=FakeIntents(), clock=lambda: NOW)


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


@pytest.mark.parametrize("line", [
    "Hello, this is a call about your car's extended warranty, press 1 to speak to an agent",
    "This is your final notice, press one now",
    "We've been trying to reach you about your vehicle's warranty",
    "Congratulations, you've been selected for a free cruise",
    "This is the IRS, there is a lawsuit filed under your name",
    "Press 1 to lower your credit card interest rate",
])
def test_robocall_is_ended_politely(brain, line):
    r = talk(brain, line)[0]
    assert r.action == "hangup" and "goodbye" in said(r)
    assert not brain.store.active()


def test_real_caller_mentioning_warranty_is_not_spam(brain):
    r = talk(brain, "Does my website come with any kind of warranty?")[0]
    assert r.action != "hangup"


def test_three_turns_going_nowhere_end_the_call(brain):
    r = talk(brain, "banana keyboard purple", "hmm", "blah blah blah", "zzz")
    assert any(x.action == "hangup" for x in r)
    assert r[0].action != "hangup"


def test_one_miss_then_a_real_question_is_fine(brain):
    r = talk(brain, "banana keyboard purple", "How much is a website?")
    assert r[1].action != "hangup"


@pytest.mark.parametrize("junk", ["Blah", "You", "Nobody", "Whatever"])
def test_junk_is_not_a_name(brain, junk):
    r = talk(brain, "Can you take a message?", junk)
    assert f", {junk.lower()}" not in said(r[1])


def test_engine_hangs_up_after_a_hangup_reply(tmp_path):
    class Spam:
        def handle(self, text, session, caller_id=None):
            return Reply("Goodbye.", "hangup", speech="Thanks, but we're not interested. Goodbye.")
    eng = CallEngine(BookingStore(str(tmp_path / "h.sqlite")), asr=MockASR(["press 1"]), tts=type(
        "T", (), {"synthesize": staticmethod(lambda t: asyncio.sleep(0, b"\xff" * 160))})(),
        dialogue_factory=lambda: Spam())
    t = FakeTransport([CallStart("C1", "+15550000001"), *[AudioIn(LOUD)] * 10, *[AudioIn(QUIET)] * 20, 5.0])
    asyncio.run(eng.run(t))
    assert ("hangup",) in t.sent


def _engine(tmp_path, **kw):
    class Talk:
        def handle(self, text, session, caller_id=None):
            return Reply("ok", "reply", speech="Okay.")
    spoken = []
    class TTS:
        async def synthesize(self, text):
            spoken.append(text); return b"\xff" * 160
    eng = CallEngine(BookingStore(str(tmp_path / "m.sqlite")), asr=MockASR(["hi"] * 50), tts=TTS(),
                     dialogue_factory=lambda: Talk(), **kw)
    return eng, spoken


def test_call_longer_than_the_limit_is_wrapped_up(tmp_path):
    eng, spoken = _engine(tmp_path, max_call_s=1.0)
    script = [CallStart("C1", "+15550000001")]
    for _ in range(6):
        script += [*[AudioIn(LOUD)] * 10, *[AudioIn(QUIET)] * 20, 0.4]
    t = FakeTransport(script + [3.0])
    asyncio.run(eng.run(t))
    assert ("hangup",) in t.sent and any("call back" in s.lower() for s in spoken)


def test_silent_open_line_is_hung_up(tmp_path):
    eng, spoken = _engine(tmp_path, idle_hangup_s=0.5)
    t = FakeTransport([CallStart("C1", "+15550000001"), *[AudioIn(QUIET)] * 5, 2.5])
    asyncio.run(eng.run(t))
    assert ("hangup",) in t.sent


def test_normal_short_call_is_not_cut(tmp_path):
    eng, spoken = _engine(tmp_path, max_call_s=30.0, idle_hangup_s=30.0)
    t = FakeTransport([CallStart("C1", "+15550000001"), *[AudioIn(LOUD)] * 10, *[AudioIn(QUIET)] * 20, 0.5])
    asyncio.run(eng.run(t))
    assert ("hangup",) not in t.sent
