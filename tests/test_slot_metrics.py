"""R2 per-slot metrics gates — Ramona (Laptop 2)."""
from receptionist.nlu import normalize
from research.data_pipeline.nlu_eval import gold_set
from research.data_pipeline.slot_metrics import parse_slots, slot_analysis


def test_parse_slots_extracts_date_time_duration():
    assert parse_slots("book 2027-12-10 14:00") == {"date": "2027-12-10",
                                                     "time": "14:00"}
    assert parse_slots("book 2027-12-10 14:00 60") == {"date": "2027-12-10",
                                                       "time": "14:00",
                                                       "duration": "60"}
    assert parse_slots("yes") == {}


def test_slot_f1_is_perfect_on_gold_bookings():
    rep = slot_analysis(normalize, gold_set())
    assert rep.analyzed >= 20
    assert rep.micro["f1"] == 1.0
    for s in ("date", "time", "duration"):
        assert rep.per_slot[s]["f1"] == 1.0
    assert rep.error_types.get("correct", 0) == rep.analyzed
    assert "wrong_date" not in rep.error_types
    assert "wrong_time" not in rep.error_types


def test_error_breakdown_flags_a_slot_blind_parser():
    """A parser that always books the same slot shows wrong_date/wrong_time."""
    def slot_blind(_utterance: str) -> str:
        return "book 2027-12-10 14:00"

    rep = slot_analysis(slot_blind, gold_set())
    assert rep.micro["f1"] < 1.0
    assert rep.error_types.get("wrong_date", 0) > 0 or \
           rep.error_types.get("wrong_time", 0) > 0


def test_missed_booking_is_counted():
    """A parser that never books shows missed_booking, not wrong slots."""
    def never_books(_utterance: str) -> str:
        return "help"

    rep = slot_analysis(never_books, gold_set())
    assert rep.error_types.get("missed_booking", 0) == rep.analyzed
    assert rep.micro["recall"] == 0.0
