"""Round 8: lawyer, Spanish landscaper, self-correcting booker, daycare, confused 85-year-old."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


def test_after_the_pitch_a_question_is_still_a_question(brain):
    r = talk(brain, "Hi, I run a two attorney law practice", "Where are the call recordings stored, and who can access them?")
    assert r[1].action not in SLOTS and ("local" in said(r[1]) or "private" in said(r[1]))


def test_callback_request_with_details_is_a_message_not_a_booking(brain):
    talk(brain, "Can I book something Friday at 2?")
    r = talk(brain, "Fine, have Alex call me. Diane Cho, 973 555 0161")
    assert not brain.store.active()
    assert brain.store.messages() or "tell" in said(r[0])


@pytest.mark.parametrize("junk", ["Fine", "Okay", "Perdón"])
def test_filler_is_not_a_name(brain, junk):
    r = talk(brain, "Can you take a message?", junk)
    assert f", {junk.lower()}" not in said(r[1])


def test_spanish_occupation_is_not_a_name(brain):
    r = talk(brain, "Hola, ¿habla español?", "Soy jardinero, quiero la recepcionista para mi negocio")
    assert "jardinero" not in said(r[1]).split(".")[0]


def test_spanish_price_after_receptionist_is_the_receptionist(brain):
    r = talk(brain, "Hola, ¿habla español?", "Quiero la recepcionista para mi negocio", "¿Cuánto cuesta?")
    assert "flexible" in said(r[2]) or "tamaño" in said(r[2])


def test_self_corrected_ordinal(brain):
    r = talk(brain, "I want to book Thursday afternoon", "The first one. No wait, the last one.")
    assert "three" in said(r[1]) or "four" in said(r[1]) or "five" in said(r[1])


def test_book_a_call_with_alex_is_booking(brain):
    r = talk(brain, "I want to book a call with Alex")
    assert r[0].action != "handoff"


def test_wrong_number_pharmacy(brain):
    r = talk(brain, "Hello, is this the pharmacy? I need my refill", "What is this place then?", "Lex what?", "Huh?")
    assert "pharmacy" in said(r[0]) and "isn't" in said(r[0])
    assert not any(x.action == "hangup" for x in r)


def test_liability(brain):
    r = talk(brain, "What if the AI gives someone legal advice by mistake? Who's liable?")
    assert "advice" in said(r[0]) and r[0].action not in SLOTS


def test_split_deposit_is_not_a_yes(brain):
    r = talk(brain, "Can I pay the deposit in two parts?")
    assert "fifty percent" in said(r[0]) and not said(r[0]).startswith("of course") and r[0].action not in SLOTS


def test_parent_login(brain):
    r = talk(brain, "I need a website with a parent login for my daycare")
    assert "portal" in said(r[0]) or "log in" in said(r[0])
