"""Round 4: a caller who says a name and number must never be lost. Numbers said anywhere are
kept, names said next to them are kept, and a call that ends with a heard lead saves a message."""
import pytest

from receptionist.booking import BookingStore
from receptionist.brain import Brain
from tests.test_kindness import CALLER, NOW, FakeIntents, said


@pytest.fixture
def brain(tmp_path):
    return Brain(BookingStore(str(tmp_path / "l.sqlite")), intents=FakeIntents(), clock=lambda: NOW)


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


def test_name_and_number_in_one_breath_at_the_name_step(brain):
    r = talk(brain, "Can you take a message?", "Kim, Bella Rose Boutique, 862 555 0119")
    assert "number" not in said(r[1]) and "tell" in said(r[1])
    r2 = talk(brain, "Tell him the site is showing an error page.")
    m = brain.store.messages()[0]
    assert m["callback"] == "8625550119" and m["name"] == "Kim"


def test_callback_request_with_number_and_name_starts_a_message(brain):
    r = talk(brain, "Have the fella call me, 201 555 0187, Walter Brennan")
    assert "what's your name" not in said(r[0]) and "walter" in said(r[0])
    assert "two oh one" in said(r[0])


def test_ending_the_call_with_a_heard_lead_saves_it(brain):
    talk(brain, "Marisol Peña, 973 555 2281", "Gracias, bye bye")
    ms = brain.store.messages()
    assert ms and ms[0]["callback"] == "9735552281"


def test_read_back_uses_the_number_they_said_not_caller_id(brain):
    r = talk(brain, "Can you take a message?", "Kim, 862 555 0119", "It's urgent, the site is down", "Read my number back")
    assert "eight six two" in said(r[3]) and "five five five, oh oh oh" not in said(r[3])
