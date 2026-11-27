"""Names are the weakest link on phone audio: after a booking, she reads the name back and lets the
caller confirm, correct, or spell it. The booking keeps the right name."""
from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


def booked(brain):
    talk(brain, "Can I book Friday at 10 am?", "Yes")


def test_name_is_read_back(brain):
    booked(brain)
    r = talk(brain, "Mike Talvato")
    assert "mike talvato" in said(r[0]) and "right" in said(r[0])


def test_yes_keeps_it(brain):
    booked(brain)
    r = talk(brain, "Mike Delgado", "Yes")
    assert brain.store.active()[0]["name"] == "Mike Delgado" and "anything else" in said(r[1])


def test_no_with_the_name_fixes_it(brain):
    booked(brain)
    talk(brain, "Mike Talvato", "No, Mike Delgado")
    assert brain.store.active()[0]["name"] == "Mike Delgado"


def test_bare_no_asks_to_spell(brain):
    booked(brain)
    r = talk(brain, "Mike Talvato", "No", "D-E-L-G-A-D-O")
    assert "spell" in said(r[1])
    assert brain.store.active()[0]["name"].endswith("Delgado")


def test_garbled_name_is_asked_once_more(brain):
    booked(brain)
    r = talk(brain, "Mike Mattel-Garo Mikezzo Garo Mikezzo Garo Mikezzo Garo Mikezzo Garo candles Hello Woody personaje norma")
    assert "name" in said(r[0]) and "again" in said(r[0])
    r = talk(brain, "Mike Delgado")
    assert "mike delgado" in said(r[0])
