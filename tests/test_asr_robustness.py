"""R2 ASR-noise robustness gates — Ramona (Laptop 2).

The safety-critical property: under ASR-style perturbation the parser must never
book the wrong slot, and a keyword must never become a booking. Retention of a
correct parse is tracked as a baseline (the Pi model should raise it).
"""
from receptionist.nlu import normalize
from research.data_pipeline.asr_noise import run_robustness


def test_never_mis_books_under_noise():
    r = run_robustness(normalize)
    assert r.unsafe == 0, r.unsafe_examples
    assert r.total >= 30


def test_keywords_never_become_bookings_under_noise():
    r = run_robustness(normalize)
    assert r.keyword_unsafe == 0, r.unsafe_examples
    assert r.keyword_total >= 20


def test_retention_baseline_is_recorded():
    """Documents the rule-based parse-retention baseline under noise."""
    r = run_robustness(normalize)
    # correct parse OR fail-safe accounts for every perturbation
    assert r.retained + r.failed_safe == r.total
    assert r.retention_rate >= 0.8, r.as_dict()


def test_robustness_detector_catches_slot_blind_parser():
    """A parser that ignores the slot books the wrong time under noise -> unsafe."""
    def slot_blind(_utterance: str) -> str:
        return "book 2027-12-10 14:00"

    r = run_robustness(slot_blind)
    assert r.unsafe > 0, "robustness check should catch a slot-blind parser"
