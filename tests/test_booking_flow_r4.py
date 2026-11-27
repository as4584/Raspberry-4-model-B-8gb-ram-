"""Round 4 booking and message flow: no name loops, a name and number answer an open offer,
'bye' is never consent, and 'book a call with Alex' books instead of giving out his number."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


def test_name_asked_at_most_twice(brain):
    r = talk(brain, "Can you take a message?", "uh", "this number is fine", "she needs online booking")
    asks = [x for x in r if "name" in said(x)]
    assert len(asks) <= 2
    assert brain.store.messages() or "tell" in said(r[-1]) or "message" in said(r[-1])


def test_bye_is_not_consent_to_book(brain):
    r = talk(brain, "Can I book something Friday at 10?", "Okay perfect, thanks, bye")
    # "Okay perfect" agrees; "bye" means wrap up: confirm and let them go, no more questions.
    assert "take care" in said(r[1]) and "name" not in said(r[1])


def test_book_a_call_with_alex_books(brain):
    r = talk(brain, "I'd like to book a call with Alex Thursday afternoon")
    assert r[0].action != "handoff" and "five five one" not in said(r[0])


def test_name_and_number_answer_an_open_offer(brain):
    # Round 13: details are kept, but the time is only booked on a clear yes.
    r = talk(brain, "Can I book something Friday at 10?", "Dana Morales, 973 555 0142")
    assert not brain.store.active() and "dana" in said(r[1]) and "book" in said(r[1])
    r = talk(brain, "Yes please")
    assert brain.store.active()
