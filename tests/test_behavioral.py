"""R2 behavioral-test gates — CheckList MFT/INV/DIR. Ramona (Laptop 2)."""
from receptionist.nlu import normalize
from research.data_pipeline.behavioral import run_behavioral


def test_behavioral_all_pass():
    r = run_behavioral(normalize)
    assert r.all_pass, r.failures


def test_invariance_holds_fully():
    """Label-preserving perturbations never change the booking."""
    r = run_behavioral(normalize)
    assert r.inv_pass == r.inv_total and r.inv_total >= 12, r.failures


def test_directional_slot_sensitivity():
    """Changing the date/time MUST change the booked slot."""
    r = run_behavioral(normalize)
    assert r.dir_pass == r.dir_total and r.dir_total >= 5, r.failures


def test_mft_every_capability():
    r = run_behavioral(normalize)
    assert r.mft_pass == r.mft_total and r.mft_total >= 8, r.failures


def test_dir_detects_a_slot_blind_parser():
    """A parser that ignores the slot and always books the same time must fail DIR."""
    def slot_blind(_utterance: str) -> str:
        return "book 2027-12-10 14:00"

    r = run_behavioral(slot_blind)
    assert r.dir_pass < r.dir_total, "DIR should catch a slot-blind parser"
