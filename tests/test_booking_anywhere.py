"""Buyer panel: three of six wanted to book and none got a time. A booking request in the middle of
taking a message books (the message details are kept), and Spanish callers can book in Spanish."""
from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked", "ask_day")


def test_booking_request_at_the_message_step_books(brain):
    r = talk(brain, "Can you take a message?", "Jay, 201 555 0144", "Actually, can you just book me Thursday morning?")
    assert r[2].action in SLOTS and "thursday" in said(r[2])


def test_booking_request_at_the_name_step_books(brain):
    r = talk(brain, "Can I leave a message?", "Book me an appointment Tuesday at 2 pm")
    assert r[1].action in SLOTS


def test_spanish_booking_offers_times_in_spanish(brain):
    r = talk(brain, "Hola, buenos días", "Quiero una cita el jueves por la mañana")
    assert r[1].action in SLOTS and "jueves" in said(r[1]) and ("nueve" in said(r[1]) or "diez" in said(r[1]) or "once" in said(r[1]))


def test_spanish_booking_confirms_and_books(brain):
    talk(brain, "Hola, buenos días")
    r = talk(brain, "¿Me puede hacer una cita el martes a las dos de la tarde?", "Sí, por favor")
    assert r[0].action == "confirm_required" and "martes" in said(r[0])
    assert r[1].action == "booked" and brain.store.active()
    assert "código" in said(r[1])


def test_spanish_pick_from_offered(brain):
    r = talk(brain, "Hola, buenos días", "Quiero una cita el jueves por la mañana", "A las diez")
    assert r[2].action in ("confirm_required", "booked")
