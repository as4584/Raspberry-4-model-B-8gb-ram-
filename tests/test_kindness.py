"""Six simulated callers (busy plumber, 74-year-old, burned salon owner, fast founder,
ESL restaurant owner, stressed baker) all scored the live brain 3/10 on 2026-11-26
(lab/sim). These are their worst moments, word for word where it matters: a caller is
heard first, can leave a message, is let go when they need to go, gets corrections
honoured, and is never told 'outside what I can help with'."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from receptionist.booking import BookingStore
from receptionist.brain import Brain

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=NY)          # Wednesday
CALLER = "+15550001000"

KEYWORDS = [("emergency", ("emergency", "fire", "down")), ("human", ("talk to alex", "someone", "person")),
            ("cancel", ("cancel",)), ("faq", ("price", "cost", "how much")),
            ("book", ("book", "appointment", "tomorrow", "friday", "tuesday", "wednesday", "move it"))]


class FakeIntents:
    def classify(self, text):
        t = text.lower()
        for intent, words in KEYWORDS:
            if any(w in t for w in words):
                return intent, 0.9
        return "faq_unknown", 0.4


@pytest.fixture
def brain(tmp_path):
    return Brain(BookingStore(str(tmp_path / "b.sqlite")), intents=FakeIntents(), clock=lambda: NOW)


def talk(brain, *turns):
    return [brain.handle(t, "call1", caller_id=CALLER) for t in turns]


def said(r):
    return (r.speech or r.text).lower()


def test_takes_a_message_with_name_and_number(brain):
    r = talk(brain, "Can you take a message for Alex?", "It's Priya.", "Yes.",
             "My website is down and I need him to call me back.")
    assert "name" in said(r[0])
    assert "priya" in said(r[1]) and "number" in said(r[1])
    assert "what" in said(r[2])
    assert r[3].action == "message_taken" and "alex" in said(r[3])
    (m,) = brain.store.messages()
    assert m["name"] == "Priya" and m["callback"] == CALLER and "website is down" in m["body"]


def test_callback_request_also_takes_a_message(brain):
    r = talk(brain, "Can you have him call me back?")
    assert r[0].action == "ask_name"


def test_a_different_callback_number(brain):
    r = talk(brain, "I want to leave a message.", "Mike.", "No, use nine seven three five five five one two one two.",
             "Need a quote for my plumbing site.")
    assert r[3].action == "message_taken"
    assert brain.store.messages()[0]["callback"] == "9735551212"


def test_site_down_is_not_a_911_call(brain):
    (r,) = talk(brain, "My website is down, it's kind of an emergency.")
    assert "nine one one" not in said(r) and "911" not in said(r)
    assert "sorry" in said(r) and r.action == "ask_name"


def test_a_real_emergency_still_gets_911(brain):
    (r,) = talk(brain, "There's a fire in the building, this is an emergency!")
    assert "nine one one" in said(r)


@pytest.mark.parametrize("words", [
    "Honestly I'm drowning, sales are way down and I don't know where to start.",
    "My last developer took three thousand dollars and vanished.",
    "I'm so stressed out, I can't afford much right now."])
def test_feelings_come_first(brain, words):
    (r,) = talk(brain, words)
    assert any(w in said(r) for w in ("sorry", "sounds", "understand", "hear that"))
    assert not said(r).startswith("nice")


@pytest.mark.parametrize("bye", ["I gotta go.", "No thanks. Bye.", "Never mind, bye bye.",
                                 "Thank you, God bless, goodbye.", "Okay I have to run.", "I'll call back later."])
def test_letting_people_go(brain, bye):
    (r,) = talk(brain, bye)
    assert r.action == "goodbye"


def test_gotta_go_mid_booking_ends_kindly(brain):
    r = talk(brain, "Can I book something tomorrow?", "Hold on, I gotta go.")
    assert r[1].action == "goodbye"


@pytest.mark.parametrize("wait", ["Hold on.", "Hold on one sec.", "Give me a second."])
def test_hold_on_waits(brain, wait):
    (r,) = talk(brain, wait)
    assert r.action == "filler"


def test_question_after_slots_is_not_which_time(brain):
    r = talk(brain, "Can I book something tomorrow?", "Wait, do you do payment plans though?")
    assert r[0].action == "offer_slots"
    assert "which time" not in said(r[1])


def test_day_correction_is_honoured(brain):
    (r,) = talk(brain, "Can I book Tuesday, no, no, Wednesday next week?")
    assert "wednesday" in said(r) and "tuesday" not in said(r)


def test_time_correction_is_honoured(brain):
    r = talk(brain, "Book tomorrow, not ten, two o'clock.")
    assert r[0].action == "confirm_required" and "two" in said(r[0])


def test_moving_a_booking_in_the_same_call_does_not_double_book(brain):
    r = talk(brain, "Book tomorrow at two p m.", "Yes.", "Actually can we move it to Friday at eleven?", "Yes.")
    assert r[1].action == "booked" and r[3].action == "rescheduled"
    (row,) = brain.store.active()
    assert row["starts"] == "2026-11-20T16:00:00+00:00"


def test_cancel_it_in_the_same_call(brain):
    r = talk(brain, "Book tomorrow at two p m.", "Yes.", "Actually, cancel it.")
    assert r[2].action == "cancelled" and "cancelled" in said(r[2])
    assert brain.store.active() == []


@pytest.mark.parametrize("q", ["Do you do SEO?", "Can I see examples of your work?", "Do you do payment plans?",
                               "Do you guys make logos?"])
def test_unknown_questions_get_an_honest_answer(brain, q):
    (r,) = talk(brain, q)
    assert "outside what i can help with" not in said(r) and "lost you" not in said(r)
    assert "alex" in said(r)


def test_talk_to_alex_offers_a_message(brain):
    (r,) = talk(brain, "Can I talk to Alex?")
    assert "message" in said(r)


def test_spanish_gets_a_straight_answer(brain):
    (r,) = talk(brain, "Do you speak Spanish?")
    assert "english" in said(r)


def test_repeat_request_is_not_like_i_said(brain):
    r = talk(brain, "How much does a website cost?", "How much does a website cost?")
    assert not said(r[1]).startswith("like i said")


def test_small_talk_is_not_a_booking(brain):
    (r,) = talk(brain, "Oh it's so cold today, isn't it?")
    assert r.action != "offer_slots"
