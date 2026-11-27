"""Round 15 buyer panel rerun: no Spanish lock, names carried over, repeated numbers snapped,
'talk to Alex at <time>' books, and the owners' real questions answered."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said
from receptionist.brain import collapse_repeats


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


def test_asking_about_spanish_in_english_stays_english(brain):
    r = talk(brain, "Can it do Spanish too, and catering orders?")
    assert r[0].action != "spanish" and "spanish" in said(r[0])


def test_english_please_switches_back(brain):
    r = talk(brain, "Hola, buenos días", "English please")
    assert r[1].action != "spanish" and "english" in said(r[1])
    r2 = talk(brain, "How much is a website?")
    assert "thousand" in said(r2[0])


def test_repeated_number_collapsed():
    assert collapse_repeats("10 10 10 10") == "10"


def test_name_in_the_booking_sentence_is_kept(brain):
    r = talk(brain, "Book me Monday at 4 pm, name's Dave Kowalski", "Yes")
    assert "can i get your name" not in said(r[1])


def test_i_just_told_you(brain):
    talk(brain, "Can you take a message?", "Rosa, 973 555 0123")
    r = talk(brain, "I just told you my name")
    assert "rosa" in said(r[0])


def test_talk_to_alex_at_a_time_books(brain):
    r = talk(brain, "Can I talk to Alex Tuesday at 11 am?")
    assert r[0].action in SLOTS


@pytest.mark.parametrize("q,want", [
    ("For emergencies, can it text me?", "text"),
    ("If my customer want cancel?", "cancel"),
    ("Is it Hippato compliant?", "hipaa"),
    ("Can it book into Dentrix?", "connect"),
    ("What if the AI gets something wrong?", "advice"),
    ("Can it tell people where the truck is today?", "where you are"),
])
def test_round15_answers(brain, q, want):
    r = talk(brain, q)[0]
    assert want in said(r) and r.action not in SLOTS, said(r)


def test_spanish_phone_orders_and_receptionist_price(brain):
    r = talk(brain, "Hola, ¿habla español?", "¿Puede tomar pedidos de pasteles por teléfono?", "¿Cuánto cuesta?")
    assert "teléfono" in said(r[1]) and "flexible" in said(r[2])


def test_talk_to_alex_day_time_with_name_books(brain):
    r = talk(brain, "I'd like to talk to Alex Monday at 2, Karen Whitfield")
    assert r[0].action in SLOTS and "five five one" not in said(r[0])


def test_name_after_the_time_is_kept(brain):
    r = talk(brain, "Thursday at 4, Mike Russo", "Yes")
    assert r[1].action == "booked" and "can i get your name" not in said(r[1])


def test_emergency_what_do_you_do(brain):
    r = talk(brain, "Say a basement is flooding at 2 AM, what does it do?")
    assert r[0].action not in SLOTS and "urgent" in said(r[0])
