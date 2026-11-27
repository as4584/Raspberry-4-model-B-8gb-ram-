"""Round 5 sims: buyers were hung up on, questions became slot offers, 'that's it' wasn't
goodbye, and once the talk is about the receptionist, price/contract mean the receptionist."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


def test_never_hang_up_on_a_caller_who_said_something_real(brain):
    r = talk(brain, "My website is down and I'm losing sales", "flurb", "glorp", "zib zab", "mmm blarg")
    assert not any(x.action == "hangup" for x in r)


def test_noise_only_call_still_ends(brain):
    r = talk(brain, "flurb", "glorp", "zib zab", "mmm blarg")
    assert any(x.action == "hangup" for x in r)


@pytest.mark.parametrize("bye", ["Yeah that's it, thanks.", "No that's it, thanks.", "Nope, that's it.", "I'm all set."])
def test_thats_it_is_goodbye(brain, bye):
    r = talk(brain, "How much is a website?", bye)
    assert r[1].action == "goodbye"


def test_number_with_a_dash_is_heard(brain):
    r = talk(brain, "Can you take a message?", "Dana, number is 973-5550142")
    assert "nine seven three" in said(r[1])


def test_my_is_not_a_name(brain):
    r = talk(brain, "Can you take a message?", "My")
    assert ", my" not in said(r[1])


def test_book_a_call_with_alex_is_booking_not_his_number(brain):
    r = talk(brain, "Book a call with Alex Thursday afternoon")
    assert r[0].action != "handoff"


def test_is_it_question_is_never_a_slot_offer(brain):
    r = talk(brain, "Is it HIPAA compliant?")
    assert r[0].action not in ("offer_slots", "confirm_required")


def test_after_the_pitch_price_is_the_receptionist_price(brain):
    r = talk(brain, "Can you answer my phone while I work?", "Okay, how much?")
    assert "size of your business" in said(r[1]) or "flexible" in said(r[1])


def test_customer_card_payments_are_not_our_deposit(brain):
    r = talk(brain, "Can my customers pay with a card, Visa, Mastercard?")
    assert "deposit" not in said(r[0]) and "card" in said(r[0])


def test_haggle_with_digits(brain):
    r = talk(brain, "Can you do it for $600?")
    assert "budget" in said(r[0])


def test_message_in_the_same_breath_is_kept(brain):
    talk(brain, "Can you take a message?", "Lisa Moretti, 973 555 0199, tell him I want online booking for my salon")
    ms = brain.store.messages()
    assert ms and "online booking" in ms[0]["body"]
