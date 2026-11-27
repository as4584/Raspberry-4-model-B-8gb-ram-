"""Round 17 buyer panel: names in any state, accented 'sí' isn't garbage, no booking on an unasked yes,
capability and emergency questions aren't slot offers, the pitch sets the receptionist topic."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


def test_long_my_name_is_after_booking(brain):
    r = talk(brain, "Can I book Monday at 2 pm?", "Yes", "My name is Karen Whitfield")
    assert "karen" in said(r[2])
    assert brain.store.active()[0].get("name") == "Karen Whitfield"


def test_name_while_waiting_for_a_yes(brain):
    r = talk(brain, "Can I book Thursday at 4 pm?", "Mike Kowalski")
    assert "mike" in said(r[1]) and "yes or a no" not in said(r[1])


def test_accented_si_is_not_garbage(brain):
    r = talk(brain, "Hola, buenos días", "¿Me puede hacer una cita el martes a las dos de la tarde?", "Sí.")
    assert r[2].action == "booked"


def test_no_booking_on_an_unasked_yes(brain):
    r = talk(brain, "Is it just one guy?", "Yes")
    assert r[1].action != "booked" and not brain.store.active()


@pytest.mark.parametrize("q", ["You can book appointment for manicure for my salon?", "What if a pipe bursts at 2 am?"])
def test_capability_and_emergency_not_slots(brain, q):
    assert talk(brain, q)[0].action not in SLOTS


def test_price_after_the_pitch(brain):
    r = talk(brain, "Can you answer my phone?", "How much?")
    assert "flexible" in said(r[1])


def test_shutterday(brain):
    r = talk(brain, "Can I book Shutterday at noon?")
    assert "saturday" in said(r[0]) or "closed" in said(r[0]) or r[0].action in SLOTS


def test_no_future_time_jargon(brain):
    r = talk(brain, "Can I book yesterday at 3 pm?")
    assert "future appointment time" not in said(r[0])
