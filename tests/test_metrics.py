"""R2 metrics tests — Vendi Score, distinct-n, coverage. Ramona (Laptop 2)."""
import math

import pytest

from research.data_pipeline.metrics import (
    NgramEmbedder,
    coverage_report,
    distinct_n,
    vendi_score,
)
from research.data_pipeline.nlu_eval import gold_set


# ---------------------------------------------------------------------------
# Vendi Score behavior
# ---------------------------------------------------------------------------

def test_vendi_identical_items_is_one():
    texts = ["book 2027-12-10 14:00"] * 8
    assert vendi_score(texts) == pytest.approx(1.0, abs=1e-6)


def test_vendi_empty_and_singleton():
    assert vendi_score([]) == 0.0
    assert vendi_score(["hello"]) == 1.0


def test_vendi_bounded_by_n():
    texts = [
        "book 2027-12-10 14:00",
        "reservar 2028-01-05 a las 9am",
        "I need a human",
        "cancelar eso",
        "what are your hours",
    ]
    vs = vendi_score(texts)
    assert 1.0 < vs <= len(texts) + 1e-6


def test_vendi_diverse_beats_redundant():
    redundant = ["book 2027-12-10 14:00", "book 2027-12-10 14:00",
                 "book 2027-12-10 14:00", "yes"]
    diverse = ["book 2027-12-10 14:00", "necesito hablar con alguien",
               "what are your hours", "cancelar eso"]
    assert vendi_score(diverse) > vendi_score(redundant)


def test_vendi_monotone_adding_novel_raises_it():
    base = ["yes", "yeah", "yep"]            # near-synonyms, low diversity
    plus_novel = base + ["book 2027-12-10 14:00", "necesito ayuda"]
    assert vendi_score(plus_novel) > vendi_score(base)


def test_ngram_embedder_is_deterministic():
    emb = NgramEmbedder(dim=256)
    a = emb.embed(["book 2027-12-10 14:00", "reservar mañana"])
    b = emb.embed(["book 2027-12-10 14:00", "reservar mañana"])
    assert (a == b).all()


# ---------------------------------------------------------------------------
# distinct-n
# ---------------------------------------------------------------------------

def test_distinct_n_all_same_is_low():
    assert distinct_n(["a b c", "a b c", "a b c"], n=2) == pytest.approx(1 / 3 * 2 / 2)


def test_distinct_n_all_unique_is_one():
    assert distinct_n(["a b", "c d", "e f"], n=2) == 1.0


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def test_coverage_on_gold_set_is_broad():
    rep = coverage_report(gold_set())
    assert rep.intent_coverage >= 0.8, rep.missing_intents
    assert rep.lang_coverage == 1.0
    assert rep.register_coverage >= 0.8
    assert rep.slot_bucket_coverage >= 0.6, rep.missing_slot_buckets
    assert rep.compositional_cells >= 15


def test_coverage_detects_narrow_dataset():
    narrow = [
        {"intent": "book", "lang": "en", "register": "formal",
         "expected_slot": "2027-12-10 14:00", "utterance": "book 2027-12-10 14:00"}
    ] * 5
    rep = coverage_report(narrow)
    assert rep.intent_coverage < 0.3
    assert rep.compositional_cells == 1
    assert "affirm" in rep.missing_intents


def test_slot_buckets_capture_time_of_day_and_dst():
    exs = [
        {"intent": "book", "lang": "en", "register": "formal",
         "expected_slot": "2027-12-10 09:00", "utterance": "book 2027-12-10 09:00"},
        {"intent": "book", "lang": "en", "register": "formal",
         "expected_slot": "2027-12-10 18:30", "utterance": "book 2027-12-10 18:30"},
        {"intent": "book", "lang": "en", "register": "formal",
         "expected_slot": "2027-03-14 02:30", "utterance": "book 2027-03-14 02:30"},
    ]
    rep = coverage_report(exs)
    hit = rep.slot_buckets_hit
    assert "time_morning" in hit
    assert "time_evening" in hit
    assert "time_half_hour" in hit
    assert "dst_edge" in hit
