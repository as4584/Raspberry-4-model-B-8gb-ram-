"""Round 10: callback times kept, second halves answered, capability questions never booked,
HVAC/nonprofit/investor needs answered, no teaching-session tangent on a website price."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


def test_callback_time_after_the_message_is_kept(brain):
    talk(brain, "Can you take a message?", "Mo, 732 555 0144", "Tell him I want a gym website")
    r = talk(brain, "Mornings are best")
    m = brain.store.messages()[0]
    assert "morning" in m["body"].lower() and "morning" in said(r[0])


def test_would_second_half_answered(brain):
    r = talk(brain, "How much is a website and how long would it take?")
    assert "thousand" in said(r[0]) and "twenty four" in said(r[0])


def test_capability_with_book_word_is_not_a_booking(brain):
    r = talk(brain, "Can it screen new clients and book consultations for me?")
    assert r[0].action not in SLOTS


@pytest.mark.parametrize("q,want", [
    ("Why the fifty percent deposit?", "started"),
    ("Where can I read reviews about you?", "free call"),
    ("Will it answer at 2 a.m. when someone has no heat?", "any time"),
    ("Can it tell an emergency from a routine call and text me the urgent ones?", "urgent"),
    ("I volunteer at a food pantry, we're a nonprofit with very little money", "nonprofit"),
    ("Can you add a volunteer sign up form?", "form"),
    ("Could the receptionist qualify sellers, like ask them the address and timeline?", "address"),
    ("How fast can you launch it?", "twenty four"),
    ("What does the ten dollars a month cover, and can I stop it?", "stop"),
    ("Would Alex sign an NDA?", "confidentiality"),
])
def test_round10_answers(brain, q, want):
    r = talk(brain, q)[0]
    assert want in said(r) and r.action not in SLOTS, said(r)


def test_website_price_has_no_teaching_tangent(brain):
    r = talk(brain, "How much is a website?")
    assert "teaching" not in said(r[0]) and "thousand" in said(r[0])


def test_lesson_price_still_works(brain):
    r = talk(brain, "How much are your automation lessons?")
    assert "hundred" in said(r[0])
