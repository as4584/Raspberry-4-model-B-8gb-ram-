"""Round 7 (hard callers): no slot offers unless they asked to book, both questions answered,
ASR repeats collapsed, names from 'tell him X', more goodbyes, warm with personal requests."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


@pytest.mark.parametrize("q", ["What's two plus two?", "Hi, this is Pastor Williams from Grace Church",
                               "Can you all do a livestream on Sunday?", "What is the charges for client portal app?",
                               "Pizza or toaster?", "I will revert"])
def test_no_slot_offers_without_a_booking_request(brain, q):
    assert talk(brain, q)[0].action not in SLOTS


def test_two_questions_two_answers(brain):
    r = talk(brain, "How much is a website and how long does it take?")
    assert "thousand" in said(r[0]) and "twenty four hours" in said(r[0])


def test_asr_repeat_loop_is_collapsed(brain):
    r = talk(brain, "How much is a website? How much is a website? How much is a website? How much is a website?")
    assert "thousand" in said(r[0])


def test_tell_him_name(brain):
    r = talk(brain, "Can you take a message?", "Tell him Brenda from Brenda's Nails")
    assert "tell" not in said(r[1]).split("?")[0].replace("tell alex", "").replace("tell him", "") or "brenda" in said(r[1])
    assert "brenda" in said(r[1])


@pytest.mark.parametrize("bye", ["I will revert", "Aight thanks, peace", "Okay, later"])
def test_more_goodbyes(brain, bye):
    talk(brain, "How much is a website?")
    assert ends_or_asks_then_ends(brain, talk, bye)


def test_whats_the_number(brain):
    r = talk(brain, "What's the number to reach Alex?")
    assert "five five one" in said(r[0])


@pytest.mark.parametrize("q,want", [
    ("Does it answer at five in the morning?", "any time"),
    ("Can clients upload documents securely in a portal?", "portal"),
    ("Can people give donations online and can you do a livestream?", "donation"),
    ("Do you have a discount for churches or nonprofits?", "nonprofit"),
    ("The last web guy took my money and disappeared, why should I trust a deposit?", "no commitment"),
])
def test_round7_answers(brain, q, want):
    assert want in said(talk(brain, q)[0])


def test_prayer_request_is_met_warmly(brain):
    r = talk(brain, "And would you pray for me, dear?")
    assert "kind" in said(r[0]) or "pass" in said(r[0])
    assert r[0].action not in SLOTS


def ends_or_asks_then_ends(brain, talk, bye):
    """A goodbye ends the call; if no details were left yet, she first asks once for a follow-up
    (owner's live call), and the next goodbye ends it."""
    r = talk(brain, bye)[0]
    if r.action == "goodbye":
        return True
    return "name and number" in (r.speech or "").lower() and talk(brain, "No thanks, bye")[0].action == "goodbye"
