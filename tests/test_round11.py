"""Round 11 (new verticals): what the receptionist can do for *their* business, on-site booking
vs booking a call, 'you called me' complaints, demos, installments, thanks, Spanish websites."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


@pytest.mark.parametrize("q", ["Can she tell my customers when their car is ready?",
                               "Could it answer insurance questions for my patients?",
                               "Can it give customers quotes on brakes?"])
def test_capability_for_their_business(brain, q):
    r = talk(brain, q)[0]
    assert "customers ask" in said(r) or "your customers" in said(r) or "questions" in said(r)
    assert r.action not in SLOTS and "don't want to guess" not in said(r)


def test_how_would_she_know(brain):
    r = talk(brain, "How would she even know that? I ain't typing nothing")
    assert "alex sets" in said(r[0])


def test_booking_on_their_site_is_a_feature(brain):
    r = talk(brain, "I want my students to book sessions on a calendar and pay online")
    assert r[0].action not in SLOTS and "online booking" in said(r[0])


def test_you_called_me(brain):
    r = talk(brain, "I got a call from this number, who is this?", "Take me off your list")
    assert "what kind of business" not in said(r[0]) and "sorry" in said(r[0])
    assert "contact" in said(r[1]) or "list" in said(r[1])


def test_book_a_demo_books(brain):
    r = talk(brain, "Can I book a demo with Alex?")
    assert "five five one" not in said(r[0])


def test_installments_after_no_plans(brain):
    r = talk(brain, "Is there a deposit?", "Can I pay in installments?")
    assert "payment plans" in said(r[1]) and "don't want to guess" not in said(r[1])


def test_call_volume_price(brain):
    r = talk(brain, "We get eighty calls a day, does the price change?")
    assert "flexible" in said(r[0]) or "calls you get" in said(r[0])


def test_plain_thanks_ends(brain):
    talk(brain, "How much is a website?")
    assert ends_or_asks_then_ends(brain, talk, "Okay, thanks")


def test_spanish_website_request(brain):
    r = talk(brain, "Hola, ¿habla español?", "Tengo un negocio de limpieza y quiero una página web")
    assert "mil dólares" in said(r[1]) or "página" in said(r[1])
    assert "no le entendí" not in said(r[1])


def test_spanish_how_it_works_is_not_the_same_pitch(brain):
    r = talk(brain, "Hola, ¿habla español?", "Quiero la recepcionista", "¿Cómo funciona?")
    assert said(r[2]) != said(r[1])


def test_spanish_greeting_isnt_pushy(brain):
    r = talk(brain, "Hola, buenos días, ¿habla español?")
    assert "número" not in said(r[0]) or "ayudar" in said(r[0])


def ends_or_asks_then_ends(brain, talk, bye):
    """A goodbye ends the call; if no details were left yet, she first asks once for a follow-up
    (owner's live call), and the next goodbye ends it."""
    r = talk(brain, bye)[0]
    if r.action == "goodbye":
        return True
    return "name and number" in (r.speech or "").lower() and talk(brain, "No thanks, bye")[0].action == "goodbye"
