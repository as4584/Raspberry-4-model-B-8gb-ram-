"""Guided design (owner, 2026-11-27): she leads the call. A menu greeting, and anything she can't place
becomes a concrete free-call offer with a real time (yes books it), then a message offer. Never a dead end."""
import pytest

from receptionist.profile import greeting_for
from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


DEAD_ENDS = ("not sure i follow", "don't want to guess", "didn't quite follow", "not sure on that one")


def test_greeting_is_a_menu():
    g = greeting_for({"name": "LexMakesIt"}).lower()
    assert "prices" in g and "free call" in g and "message" in g


@pytest.mark.parametrize("odd", ["My cousin said something about you", "what's the deal with that thing",
                                 "purple elephant quantum soup", "so yeah the thing with the stuff"])
def test_anything_unplaced_becomes_a_concrete_offer(brain, odd):
    r = talk(brain, odd)[0]
    assert not any(d in said(r) for d in DEAD_ENDS), said(r)
    assert " a m" in said(r) or " p m" in said(r) or "free call" in said(r)


def test_yes_to_the_funnel_offer_books(brain):
    talk(brain, "My cousin said something about you", "yes", "yes")
    assert brain.store.active()


def test_second_miss_offers_a_message_too(brain):
    r = talk(brain, "My cousin said something about you", "flurble the wazzit with the thing")
    assert "message" in said(r[1]) and not any(d in said(r[1]) for d in DEAD_ENDS)


def test_known_answers_still_answered(brain):
    r = talk(brain, "How much is a website?")
    assert "thousand" in said(r[0])
