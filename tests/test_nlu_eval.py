"""R2 NLU evaluation gates — Ramona (Laptop 2).

Grades receptionist.nlu.normalize against the frozen, tiered, oracle-labeled
benchmark in research/data_pipeline/nlu_eval.py. These gates run off-Pi, in
milliseconds, and encode the reliability contract:

  * Tier 1 (canonical)  — command + action accuracy MUST be 100%.
  * Tier 2 (natural)     — command + action accuracy MUST be 100%.
  * Tier 3 (model-req.)  — rule-based is ALLOWED to miss the booking, but MUST
                           fail safe: zero unsafe (no invented / wrong bookings).
  * SAFETY (all tiers)   — unsafe == 0, always. Non-negotiable.

Tier 3 booking extraction is intentionally NOT gated here: it is the baseline
(currently 0%) the local Pi model must raise. See docs/evidence/r2_nlu_eval.md.

Environment: LAPTOP / MOCK / no API keys / no audio / no network.
"""
import re

import pytest

from receptionist.nlu import normalize
from research.data_pipeline.nlu_eval import (
    evaluate,
    evaluate_example,
    gold_set,
)


@pytest.fixture(scope="module")
def summary():
    return evaluate(normalize)


# ---------------------------------------------------------------------------
# Safety invariant — the one that matters most
# ---------------------------------------------------------------------------

def test_zero_unsafe_across_all_tiers(summary):
    """No example may invent a booking or book the wrong slot — ever."""
    assert summary["unsafe_total"] == 0, summary["unsafe_examples"]


def test_every_tier_is_fail_safe(summary):
    for tier, m in summary["by_tier"].items():
        assert m["unsafe"] == 0, f"tier {tier} produced unsafe outcomes"
        assert m["safe_rate"] == 1.0, f"tier {tier} not fully fail-safe"


# ---------------------------------------------------------------------------
# Tier gates
# ---------------------------------------------------------------------------

def test_tier1_perfect(summary):
    t1 = summary["by_tier"][1]
    assert t1["command_acc"] == 1.0, "Tier 1 command accuracy must be 100%"
    assert t1["action_acc"] == 1.0, "Tier 1 action accuracy must be 100%"


def test_tier2_perfect(summary):
    t2 = summary["by_tier"][2]
    assert t2["command_acc"] == 1.0, "Tier 2 command accuracy must be 100%"
    assert t2["action_acc"] == 1.0, "Tier 2 action accuracy must be 100%"


def test_tier1_and_tier2_bookings_fully_extracted(summary):
    """Everything that should book, does — in both languages."""
    assert summary["by_tier"][1]["extraction_rate"] == 1.0
    assert summary["by_tier"][2]["extraction_rate"] == 1.0


def test_tier3_baseline_is_recorded(summary):
    """Tier 3 is the model benchmark: rule-based extracts ~0 but stays safe.

    This documents the baseline rather than gating on it. If a future change
    raises extraction above 0 *and* keeps unsafe at 0, that is an improvement
    and this assertion should be revisited (not a silent regression)."""
    t3 = summary["by_tier"][3]
    assert t3["unsafe"] == 0
    assert t3["extraction_rate"] == 0.0, (
        "rule-based unexpectedly parsed a model-required booking; "
        "verify it is correct and safe, then update the baseline"
    )


# ---------------------------------------------------------------------------
# Coverage / diversity of the dataset itself
# ---------------------------------------------------------------------------

def test_dataset_is_diverse():
    gold = gold_set()
    assert len(gold) >= 100, "benchmark should be substantial"
    tiers = {ex.tier for ex in gold}
    assert tiers == {1, 2, 3}
    langs = {ex.lang for ex in gold}
    assert {"en", "es", "mixed"} <= langs
    registers = {ex.register for ex in gold}
    # A spread of registers — not just canonical commands.
    assert {"formal", "casual", "keyword", "punct", "filler",
            "relative", "spelled"} <= registers
    intents = {ex.intent for ex in gold}
    assert {"book", "affirm", "deny", "cancel", "handoff", "help",
            "unknown"} <= intents


def test_dataset_ids_unique():
    gold = gold_set()
    ids = [ex.id for ex in gold]
    assert len(ids) == len(set(ids))


def test_dataset_contains_no_pii():
    """No phone numbers, emails, or long digit runs in any utterance."""
    phone = re.compile(r"(?:\+?\d[\d\-\s().]{6,}\d)")
    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    for ex in gold_set():
        # Allow the ISO date/time and the fabricated all-zero UUID.
        scrub = ex.utterance.replace("2027-12-10", "").replace("2027-03-14", "")
        scrub = scrub.replace("2027-11-07", "")
        scrub = re.sub(r"0{8}-0{4}-0{4}-0{4}-0{12}", "", scrub)
        assert not phone.search(scrub), f"possible phone in {ex.id}: {ex.utterance!r}"
        assert not email.search(ex.utterance), f"email in {ex.id}"


# ---------------------------------------------------------------------------
# The evaluator's safety detector must actually detect unsafe behavior
# ---------------------------------------------------------------------------

def test_safety_detector_catches_a_hallucinating_normalizer():
    """A broken normalizer that books everything must be flagged unsafe.

    This guards the guard: proves the unsafe metric is not vacuously zero."""
    def book_everything(_utterance: str) -> str:
        return "book 2027-12-10 14:00"

    bad = evaluate(book_everything)
    assert bad["unsafe_total"] > 0, (
        "evaluator failed to flag a normalizer that invents bookings"
    )


def test_safety_detector_catches_wrong_slot():
    """Booking the right intent at the wrong time is unsafe."""
    # An example that should book 09:00; normalizer emits 14:00 instead.
    target = next(ex for ex in gold_set()
                  if ex.expected_slot == "2027-12-10 09:00"
                  and ex.expected_action == "confirm_required")

    def wrong_time(_utterance: str) -> str:
        return "book 2027-12-10 14:00"

    result = evaluate_example(target, wrong_time)
    assert result.unsafe, "wrong-slot booking should be flagged unsafe"
