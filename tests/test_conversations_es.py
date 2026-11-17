"""End-to-end Spanish conversation simulation tests — Ramona (Laptop 2).

Each test drives a Spanish-language dialogue through normalize() →
Dialogue.handle() and asserts the booking outcome.

This is the Spanish "teaching" dataset: it proves that the normalize()
layer correctly converts Spanish natural language into structured commands
the booking engine understands — without any LLM.

Environment: LAPTOP / MOCK / no API keys
"""
import pytest
from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue
from receptionist.nlu import normalize


def fresh(tmp_path, name="conv_es.sqlite"):
    db = BookingStore(str(tmp_path / name))
    d = Dialogue(db)
    return db, d


def turn(d, utterance, session="s1"):
    return d.handle(normalize(utterance), session)


# ---------------------------------------------------------------------------
# Happy path: Spanish booking → Spanish confirmation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("book_phrase", [
    "reservar 2027-12-10 14:00",
    "quiero reservar el 2027-12-10 a las 14:00",
    "hacer una cita 2027-12-10 14:00",
    "agendar 2027-12-10 14:00",
    "quisiera una cita el 2027-12-10 a las 9:00",
    "me gustaría reservar 2027-12-10 a las 2pm",
    "necesito una cita 2027-12-10 15:30",
    "programar 2027-12-10 11:00",
])
def test_spanish_book_then_yes_creates_booking(tmp_path, book_phrase):
    db, d = fresh(tmp_path, f"es_book_{hash(book_phrase)}.sqlite")
    r1 = turn(d, book_phrase)
    assert r1.action == "confirm_required", \
        f"Expected confirm_required for {book_phrase!r}, got {r1.action!r}"
    r2 = turn(d, "sí")
    assert r2.action == "booked"
    assert len(db.active()) == 1


@pytest.mark.parametrize("yes_phrase", [
    "sí", "si", "sí, confirmo", "de acuerdo", "está bien",
    "claro", "claro que sí", "perfecto", "adelante", "confirmar",
])
def test_spanish_confirmation_phrases(tmp_path, yes_phrase):
    db, d = fresh(tmp_path, f"es_yes_{hash(yes_phrase)}.sqlite")
    turn(d, "reservar 2027-12-10 14:00")
    r = turn(d, yes_phrase)
    assert r.action == "booked"
    assert len(db.active()) == 1


@pytest.mark.parametrize("no_phrase", [
    "no", "no gracias", "no, gracias",
    "cancelar eso", "olvídalo", "en realidad no",
])
def test_spanish_denial_phrases(tmp_path, no_phrase):
    db, d = fresh(tmp_path, f"es_no_{hash(no_phrase)}.sqlite")
    turn(d, "reservar 2027-12-10 14:00")
    r = turn(d, no_phrase)
    assert db.active() == []


# ---------------------------------------------------------------------------
# Human escalation — Spanish
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "necesito hablar con alguien",
    "quiero hablar con un humano",
    "agente por favor",
    "operador",
])
def test_spanish_human_escalation(tmp_path, phrase):
    db, d = fresh(tmp_path)
    r = turn(d, phrase)
    assert r.action == "handoff"
    assert db.active() == []


# ---------------------------------------------------------------------------
# Help — Spanish
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "ayuda", "necesito ayuda", "cómo funciona",
])
def test_spanish_help_phrases(tmp_path, phrase):
    db, d = fresh(tmp_path)
    r = turn(d, phrase)
    assert r.action == "help"


# ---------------------------------------------------------------------------
# Cancel by reference — Spanish
# ---------------------------------------------------------------------------

def test_spanish_cancel_by_reference(tmp_path):
    db, d = fresh(tmp_path)
    turn(d, "reservar 2027-12-10 14:00")
    r = turn(d, "sí")
    assert r.action == "booked"
    ref = r.committed_id
    r2 = turn(d, f"cancelar {ref}")
    assert r2.action == "cancelled"
    assert db.active() == []


def test_spanish_cancel_fabricated_reference(tmp_path):
    db, d = fresh(tmp_path)
    r = turn(d, "cancelar 00000000-0000-0000-0000-000000000000")
    assert r.action == "not_found"
    assert db.active() == []


# ---------------------------------------------------------------------------
# 12-hour clock (Spanish "a las Xpm")
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,expected_time", [
    ("reservar 2027-12-10 a las 2pm",  "02:00 PM"),
    ("reservar 2027-12-10 a las 9am",  "09:00 AM"),
    ("agendar 2027-12-10 a las 3:30pm","03:30 PM"),
])
def test_spanish_12h_clock(tmp_path, phrase, expected_time):
    db, d = fresh(tmp_path, f"es_12h_{hash(phrase)}.sqlite")
    r = turn(d, phrase)
    assert r.action == "confirm_required"
    assert expected_time in r.text


# ---------------------------------------------------------------------------
# DST edge cases (Spanish)
# ---------------------------------------------------------------------------

def test_spanish_dst_spring_forward(tmp_path):
    db, d = fresh(tmp_path)
    r = turn(d, "reservar 2027-03-14 02:30")
    assert r.action == "invalid"
    assert db.active() == []


def test_spanish_dst_fall_back(tmp_path):
    db, d = fresh(tmp_path)
    r = turn(d, "reservar 2027-11-07 01:30")
    assert r.action == "invalid"
    assert db.active() == []


# ---------------------------------------------------------------------------
# Multi-turn Spanish conversations
# ---------------------------------------------------------------------------

def test_spanish_book_deny_rebook(tmp_path):
    db, d = fresh(tmp_path)
    turn(d, "reservar 2027-12-10 14:00")
    turn(d, "no gracias")
    assert db.active() == []
    turn(d, "agendar 2027-12-11 09:00")
    r = turn(d, "de acuerdo")
    assert r.action == "booked"
    assert "2027-12-11" in db.active()[0]["starts"]


def test_mixed_es_en_conversation(tmp_path):
    """Code-switching: Spanish booking, English confirmation."""
    db, d = fresh(tmp_path)
    r1 = turn(d, "quiero reservar el 2027-12-10 a las 14:00")
    assert r1.action == "confirm_required"
    r2 = turn(d, "yes")  # English confirmation
    assert r2.action == "booked"
    assert len(db.active()) == 1


def test_mixed_en_es_conversation(tmp_path):
    """Code-switching: English booking, Spanish confirmation."""
    db, d = fresh(tmp_path)
    r1 = turn(d, "book 2027-12-10 at 2pm")
    assert r1.action == "confirm_required"
    r2 = turn(d, "sí")  # Spanish confirmation
    assert r2.action == "booked"
    assert len(db.active()) == 1


def test_spanish_yes_no_pending_is_safe(tmp_path):
    db, d = fresh(tmp_path)
    r = turn(d, "sí")
    assert r.action == "reply"
    assert db.active() == []


def test_spanish_conversation_no_pii(tmp_path):
    """Confirm Spanish flow never surfaces credentials or internal IDs unexpectedly."""
    db, d = fresh(tmp_path)
    turn(d, "reservar 2027-12-10 14:00")
    r = turn(d, "sí")
    assert r.action == "booked"
    # Reference is a UUID — not a phone number or email
    import re
    assert re.match(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        r.committed_id or ""
    ), f"Unexpected reference format: {r.committed_id!r}"
