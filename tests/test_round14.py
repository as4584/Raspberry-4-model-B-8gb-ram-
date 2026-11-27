"""Round 14 buyer panel: receptionist price/what-it-does, name corrections, languages, their customers
cancelling, texting the owner, generic intake, any trade, references, Alex is real and local, spoken times."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


def test_what_does_this_thing_do(brain):
    assert "talking to one" in said(talk(brain, "So what's this thing do?")[0])


def test_receptionist_price_after_what_it_does(brain):
    r = talk(brain, "What's this thing do?", "Alright, give me a number, how much?")
    assert "flexible" in said(r[1])


def test_name_correction(brain):
    talk(brain, "Can you take a message?", "Microsoft, 973 555 0101")
    r = talk(brain, "No, not Microsoft. Mike Russo.")
    assert "mike" in said(r[0]) and "microsoft" not in said(r[0])


@pytest.mark.parametrize("q", ["Do you speak Vietnamese?", "Can she speak Chinese?"])
def test_languages(brain, q):
    r = talk(brain, q)[0]
    assert "english and spanish" in said(r)


def test_their_customers_cancelling(brain):
    r = talk(brain, "If my customer want to cancel their appointment, she can do?")
    assert "no refunds" not in said(r[0]) and ("cancel" in said(r[0]) or "move" in said(r[0]))


def test_texts_the_owner(brain):
    r = talk(brain, "Does it text me when somebody calls?")
    assert "text" in said(r[0]) and r[0].action not in SLOTS


def test_intake_isnt_about_cars_for_a_dentist(brain):
    r = talk(brain, "Would she ask patients for insurance or medical details?")
    assert "car" not in said(r[0])


def test_any_trade(brain):
    r = talk(brain, "What would the receptionist do for a landscaper like me?")
    assert "talking to one" in said(r[0]) or "calls" in said(r[0])


def test_references(brain):
    r = talk(brain, "Do you have any references I can call?")
    assert "don't want to guess" not in said(r[0]) and "free call" in said(r[0])


def test_alex_is_real_and_local(brain):
    r = talk(brain, "Is Alex even a real person?")
    assert "new jersey" in said(r[0]) or "paterson" in said(r[0])


def test_spoken_two_pm(brain):
    r = talk(brain, "Can I book Tuesday at to PM?")
    assert r[0].action in SLOTS and "two p m" in said(r[0])
