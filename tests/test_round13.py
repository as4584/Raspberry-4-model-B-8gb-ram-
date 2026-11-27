"""Round 13: never book without a yes, receptionist demos and capabilities, client deposits,
daytime after an evening note."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


def test_details_at_an_offer_never_book_without_yes(brain):
    talk(brain, "Can I book something Friday at 1?")
    r = talk(brain, "Ahmed, 201 555 0123")
    assert not brain.store.active() and "ahmed" in said(r[0])


def test_greeting_demo(brain):
    r = talk(brain, "Can you show me how it would greet my callers?")
    assert "thank you for calling" in said(r[0])


@pytest.mark.parametrize("q,want", [
    ("Could one receptionist answer for both of my locations?", "location"),
    ("Can it take pizza orders by phone?", "order"),
    ("Can it take claim information after hours?", "claim"),
    ("Can it handle a bunch of calls at once on a Friday rush?", "same time"),
])
def test_receptionist_capabilities(brain, q, want):
    r = talk(brain, q)[0]
    assert want in said(r) and r.action not in SLOTS


def test_client_deposit_through_cash_app(brain):
    r = talk(brain, "Can my clients pay the deposit through Cash App or Square?")
    assert "fifty percent" not in said(r[0]) and "deposit" in said(r[0])


def test_daytime_after_evening_note_offers_times(brain):
    r = talk(brain, "Can I book a call Tuesday evening?", "Okay, daytime works then. What do you have?")
    assert r[1].action in ("offer_slots", "ask_day")


def test_set_up_a_call_is_booking(brain):
    r = talk(brain, "Can I set up a call with Alex?")
    assert r[0].action in ("offer_slots", "ask_day", "confirm_required")
