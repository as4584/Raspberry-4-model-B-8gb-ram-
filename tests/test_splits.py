"""R2 grouped-split gates — leakage guard + reproducibility. Ramona (Laptop 2)."""
from dataclasses import replace

from research.data_pipeline.diversity_engine import generate_pool, select_diverse
from research.data_pipeline.nlu_eval import NLUExample, gold_set
from research.data_pipeline.splits import (
    SPLITS,
    cross_dataset_overlap,
    family_key,
    make_splits,
    split_dataset,
    split_report,
    straddling_families,
)
import pytest


@pytest.fixture(scope="module")
def grown():
    return select_diverse(generate_pool(), 240)


# ---------------------------------------------------------------------------
# Family grouping
# ---------------------------------------------------------------------------

def test_paraphrases_of_same_slot_share_a_family():
    a = NLUExample("a", "book 2027-12-10 14:00", "en", "formal", "book", 1,
                   "confirm_required", "book 2027-12-10 14:00", "2027-12-10 14:00")
    b = replace(a, id="b", utterance="I'd like to book 2027-12-10 at 2pm",
                register="casual")
    c = replace(a, id="c", utterance="reservar 2027-12-10 a las 2pm", lang="es")
    assert family_key(a) == family_key(b) == family_key(c)


def test_keyword_synonyms_share_a_family():
    y1 = NLUExample("y1", "yes", "en", "keyword", "affirm", 1, "reply", "yes", None)
    y2 = replace(y1, id="y2", utterance="yeah")
    assert family_key(y1) == family_key(y2)


def test_different_slots_are_different_families():
    a = NLUExample("a", "book 2027-12-10 14:00", "en", "formal", "book", 1,
                   "confirm_required", "book 2027-12-10 14:00", "2027-12-10 14:00")
    b = replace(a, id="b", utterance="book 2027-12-11 09:00",
                expected_command="book 2027-12-11 09:00", expected_slot="2027-12-11 09:00")
    assert family_key(a) != family_key(b)


# ---------------------------------------------------------------------------
# Leakage guard — the core property
# ---------------------------------------------------------------------------

def test_grouped_split_has_zero_family_leakage(grown):
    assign = make_splits(grown, mode="grouped")
    assert straddling_families(grown, assign) == []


def test_every_example_assigned_once(grown):
    assign = make_splits(grown, mode="grouped")
    assert set(assign.values()) <= set(SPLITS)
    assert len(assign) == len({e.id for e in grown}) == len(grown)


def test_splits_are_deterministic(grown):
    a = make_splits(grown, mode="grouped")
    b = make_splits(grown, mode="grouped")
    assert a == b


def test_grouped_split_ratios_reasonable(grown):
    rep = split_report(grown, mode="grouped")
    assert rep["sizes"]["train"] > rep["sizes"]["dev"]
    assert rep["sizes"]["holdout"] > 0 and rep["sizes"]["dev"] > 0
    assert rep["straddling_families"] == []


def test_every_intent_present_in_train(grown):
    parts = split_dataset(grown, mode="grouped")
    train_intents = {e.intent for e in parts["train"]}
    all_intents = {e.intent for e in grown}
    assert all_intents <= train_intents


# ---------------------------------------------------------------------------
# IID complement — keyword intents reach dev/holdout
# ---------------------------------------------------------------------------

def test_iid_spreads_keyword_intents_across_splits(grown):
    parts = split_dataset(grown, mode="iid")
    # affirm has one family; under iid its variants should reach >1 split
    affirm_splits = {s for s in SPLITS
                     if any(e.intent == "affirm" for e in parts[s])}
    assert len(affirm_splits) >= 2


# ---------------------------------------------------------------------------
# Cross-dataset contamination check
# ---------------------------------------------------------------------------

def test_cross_dataset_overlap_detects_shared_families():
    g = gold_set()
    assert cross_dataset_overlap(g, g)              # a set overlaps itself
    disjoint = [NLUExample("z", "book 2099-12-31 23:00", "en", "formal", "book",
                           1, "confirm_required", "book 2099-12-31 23:00",
                           "2099-12-31 23:00")]
    # a far-future unique slot shares no family with the gold set
    assert "slot:2099-12-31 23:00" not in cross_dataset_overlap(disjoint, g)
