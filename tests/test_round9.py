"""Round 9 retest of the worst callers: capability/price questions never become bookings, policy
answers exist, time words aren't names, wrong-number callers get gentle help, not a pitch."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


@pytest.mark.parametrize("q", ["Could it screen clients for conflicts and book consultations?",
                               "What would the price be for a two attorney firm?",
                               "Would it handle class booking and payments?"])
def test_capability_and_price_questions_never_book(brain, q):
    assert talk(brain, q)[0].action not in SLOTS


def test_can_i_cancel_is_policy_not_a_booking_code(brain):
    r = talk(brain, "If I sign up, can I cancel anytime, and do I own it?")
    assert "booking code" not in said(r[0]) and "cancel" in said(r[0])


@pytest.mark.parametrize("q,want", [
    ("Do I get my money back if I'm not happy?", "refund"),
    ("Why do you need a fifty percent deposit?", "started"),
    ("Is this call recorded? Is it used to train AI?", "train"),
    ("Is there a contract or a confidentiality agreement?", "contract"),
])
def test_policy_answers(brain, q, want):
    assert want in said(talk(brain, q)[0])


def test_time_words_are_not_names(brain):
    r = talk(brain, "Can you take a message?", "Mornings are best")
    assert ", mornings" not in said(r[1])


def test_wrong_number_caller_is_never_pitched(brain):
    r = talk(brain, "Is this the pharmacy?", "Is this CVS?", "I just need my medicine, can you help?", "What was that number?")
    for x in r[1:]:
        assert "what kind of business" not in said(x) and x.action not in SLOTS
        assert "pharmacy" in said(x) or "pill bottle" in said(x)


def test_burned_caller_isnt_told_about_no_portfolio(brain):
    r = talk(brain, "My last web guy scammed me. Why should I trust you?")
    assert "portfolio" not in said(r[0])


def test_brenda_from_brendas_nails(brain):
    r = talk(brain, "Can you take a message?", "Tell him Brenda from Brenda's Nails, 201 555 0199")
    assert "brenda" in said(r[1]) and "your name" not in said(r[1])


def test_spanish_blessing_heard_wrong_is_goodbye(brain):
    r = talk(brain, "Hola, necesito una página web", "Gracias mi amor, que Dios le vendida")
    assert r[1].action == "goodbye"


def test_spanish_deposit_doesnt_volunteer_no_plans(brain):
    r = talk(brain, "Hola, necesito una página web", "¿Hay que dar un depósito?")
    assert "planes" not in said(r[1])
