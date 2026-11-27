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
    assert junk.lower() not in said(r[1]).replace("thank you", "")


def test_engine_hangs_up_after_a_hangup_reply(tmp_path):
    class Spam:
        def handle(self, text, session, caller_id=None):
            return Reply("Goodbye.", "hangup", speech="Thanks, but we're not interested. Goodbye.")
    eng = CallEngine(BookingStore(str(tmp_path / "h.sqlite")), asr=MockASR(["press 1"]), tts=type(
        "T", (), {"synthesize": staticmethod(lambda t: asyncio.sleep(0, b"\xff" * 160))})(),
        dialogue_factory=lambda: Spam())
    t = FakeTransport([CallStart("C1", "+15550000001"), *[AudioIn(LOUD)] * 10, *[AudioIn(QUIET)] * 20, 1.0])
    asyncio.run(eng.run(t))
    assert ("hangup",) in t.sent
