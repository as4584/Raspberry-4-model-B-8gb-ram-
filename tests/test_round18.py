"""Round 18 buyer panel: names in long sentences while a yes is pending, spelled names, 4:30,
Spanish booking without 'cita', HIPAA as 'hip-hop', Dentrix isn't the pitch, owners' 2am calls."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said
from receptionist.brain import collapse_repeats


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


def test_my_name_is_while_a_yes_is_pending(brain):
    r = talk(brain, "Can I book Tuesday at 3:30 pm?", "My name is Karen Whitfield")
    assert "karen" in said(r[1]) and "not sure i follow" not in said(r[1])


def test_spelled_name():
    assert "Whitfield" in collapse_repeats("It's Karen W-H-I-T-F-I-E-L-D")


def test_four_thirty(brain):
    r = talk(brain, "Can I book Wednesday for 30 in the afternoon?")
    assert "four thirty" in said(r[0]) or r[0].action in SLOTS and "thirty" in said(r[0])


def test_spanish_day_and_time_without_cita(brain):
    r = talk(brain, "Hola, buenos días", "El miércoles a las once")
    assert r[1].action in SLOTS and "miércoles" in said(r[1])


def test_hip_hop_is_hipaa(brain):
    assert "hipaa" in said(talk(brain, "Is it hip-hop compliant?")[0])


def test_dentrix_isnt_the_pitch(brain):
    r = talk(brain, "Can it book directly into Dentrix?")
    assert "connect" in said(r[0])


def test_owner_with_2am_calls_is_a_prospect(brain):
    r = talk(brain, "I get emergency calls at 2 am all the time, what would it do?")
    assert "sorry you're dealing" not in said(r[0]) and "urgent" in said(r[0])
