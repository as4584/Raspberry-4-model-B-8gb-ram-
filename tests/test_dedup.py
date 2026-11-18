"""R2 near-duplicate detection gates — Ramona (Laptop 2)."""
from dataclasses import replace

from research.data_pipeline.dedup import (
    dedup_report,
    near_duplicate_pairs,
    near_duplicates_across,
)
from research.data_pipeline.diversity_engine import generate_pool, select_diverse
from research.data_pipeline.nlu_eval import NLUExample, gold_set


def _ex(i, utt, intent="book", slot="2027-12-10 14:00"):
    return NLUExample(f"x{i}", utt, "en", "formal", intent, 1,
                      "confirm_required", f"book {slot}" if slot else None, slot)


def test_detects_planted_near_duplicate():
    a = _ex(1, "book 2027-12-10 14:00")
    b = _ex(2, "um book 2027-12-10 14:00")             # filler-perturbed near-dup
    c = _ex(3, "necesito hablar con alguien", intent="handoff", slot=None)
    pairs = near_duplicate_pairs([a, b, c], threshold=0.9)
    ids = {(p["a_id"], p["b_id"]) for p in pairs}
    assert ("x1", "x2") in ids
    assert not any("x3" in (p["a_id"], p["b_id"]) for p in pairs)


def test_distinct_slots_are_not_near_duplicates():
    a = _ex(1, "book 2027-12-10 14:00")
    b = _ex(2, "reservar 2028-07-04 09:00", slot="2028-07-04 09:00")
    assert near_duplicate_pairs([a, b], threshold=0.92) == []


def test_effective_unique_counts_exact_dup_pair():
    a = _ex(1, "book 2027-12-10 14:00")
    b = _ex(2, "book 2027-12-10 14:00")                # exact dup
    c = _ex(3, "book 2028-07-04 09:00", slot="2028-07-04 09:00")
    rep = dedup_report([a, b, c], threshold=0.92)
    assert rep.effective_unique == 2
    assert rep.duplicate_clusters == 1


def test_grown_set_is_low_redundancy():
    grown = select_diverse(generate_pool(), 240)
    rep = dedup_report(grown)
    # diversity selection should keep near-dups rare
    assert rep.effective_unique >= int(0.9 * rep.n), rep.as_dict()


def test_cross_dataset_near_dups_flag_contamination():
    a = _ex(1, "book 2027-12-10 14:00")
    # same canonical command appears in the gold set -> cross-dup
    hits = near_duplicates_across([a], gold_set(), threshold=0.92)
    assert len(hits) >= 1
