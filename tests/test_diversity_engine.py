"""R2 diversity-engine gates — Ramona (Laptop 2).

Verifies the hybrid engine's safety, determinism, coverage, and diversity, and
proves the critic drops anything that would mis-book (deterministic OR LLM).
"""
import re

import pytest

from receptionist.nlu import normalize
from research.data_pipeline.augment import DeterministicAugmenter, Seed, Variant
from research.data_pipeline.diversity_engine import (
    build_seeds,
    critic,
    generate_pool,
    quality_report,
    select_diverse,
)
from research.data_pipeline.metrics import coverage_report, vendi_score


@pytest.fixture(scope="module")
def pool():
    return generate_pool()


@pytest.fixture(scope="module")
def selected(pool):
    return select_diverse(pool, target=240)


# ---------------------------------------------------------------------------
# Safety — the non-negotiable gate
# ---------------------------------------------------------------------------

def test_pool_has_zero_unsafe(pool):
    from research.data_pipeline.nlu_eval import evaluate
    assert evaluate(normalize, pool)["unsafe_total"] == 0


def test_selected_has_zero_unsafe(selected):
    from research.data_pipeline.nlu_eval import evaluate
    assert evaluate(normalize, selected)["unsafe_total"] == 0


def test_critic_drops_keyword_that_becomes_a_booking():
    """A 'yes' seed whose variant parses as a booking must be dropped."""
    seed = Seed("affirm", "en", "yes", None, "reply", "yes")
    bad = Variant("book 2027-12-10 14:00", "keyword", 1, True, True, "llm")
    assert critic(seed, bad, normalize) is None


def test_critic_drops_wrong_slot_booking():
    """A booking variant that resolves to a different time is unsafe -> dropped."""
    seed = Seed("book", "en", "book 2027-12-10 14:00", "2027-12-10 14:00",
                "confirm_required", "book 2027-12-10 14:00")
    bad = Variant("book 2027-12-10 09:00", "casual", 2, True, False, "llm")
    assert critic(seed, bad, normalize) is None


def test_critic_keeps_parse_breaking_as_safe_tier3():
    """A relative-date variant can't be parsed by rules; kept as tier-3, safe."""
    seed = Seed("book", "en", "book 2027-12-10 14:00", "2027-12-10 14:00",
                "confirm_required", "book 2027-12-10 14:00")
    hard = Variant("book tomorrow at 2pm", "relative", 3, False, False, "det")
    ex = critic(seed, hard, normalize)
    assert ex is not None
    assert ex.tier == 3
    assert ex.expected_command is None
    assert ex.expected_action == "confirm_required"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_generation_is_deterministic():
    a = [(e.id, e.utterance) for e in generate_pool()]
    b = [(e.id, e.utterance) for e in generate_pool()]
    assert a == b


def test_selection_is_deterministic(pool):
    a = [e.id for e in select_diverse(pool, 120)]
    b = [e.id for e in select_diverse(pool, 120)]
    assert a == b


def test_selection_respects_target_and_is_subset(pool):
    sel = select_diverse(pool, 100)
    assert len(sel) == 100
    pool_ids = {e.id for e in pool}
    assert all(e.id in pool_ids for e in sel)
    assert len(select_diverse(pool, len(pool) + 50)) == len(pool)


# ---------------------------------------------------------------------------
# Coverage & diversity objectives
# ---------------------------------------------------------------------------

def test_selected_maximizes_coverage(selected):
    rep = coverage_report(selected)
    assert rep.intent_coverage == 1.0, rep.missing_intents
    assert rep.lang_coverage == 1.0, rep.lang_distribution
    assert rep.register_coverage == 1.0
    assert rep.slot_bucket_coverage == 1.0, rep.missing_slot_buckets
    assert rep.compositional_cells >= 35


def test_selected_is_language_balanced(selected):
    dist = coverage_report(selected).lang_distribution
    assert dist.get("mixed", 0) >= 10, dist      # code-switch represented
    assert dist.get("en", 0) >= 50 and dist.get("es", 0) >= 50


def test_selection_improves_density_over_redundant_pool(pool, selected):
    """Selected subset is denser (higher effective diversity ratio) than pool."""
    vs_sel = vendi_score([e.utterance for e in selected])
    vs_pool = vendi_score([e.utterance for e in pool])
    assert vs_sel / len(selected) > vs_pool / len(pool)


def test_dataset_ids_unique_and_no_pii(pool):
    ids = [e.id for e in pool]
    assert len(ids) == len(set(ids))
    phone = re.compile(r"\+?\d[\d\-\s().]{6,}\d")
    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    for e in pool:
        # Strip known synthetic date/time/ref tokens (incl. typo-corrupted
        # dates like "2o27-12-10") before scanning for real PII.
        scrub = re.sub(r"0{8}-0{4}-0{4}-0{4}-0{12}", "", e.utterance)
        scrub = re.sub(r"[0-9o]{2,4}-[0-9o]{2}-[0-9o]{2}", "", scrub, flags=re.I)
        scrub = re.sub(r"[0-9o]{1,2}:[0-9o]{2}", "", scrub, flags=re.I)
        assert not phone.search(scrub), e.utterance
        assert not email.search(e.utterance)


# ---------------------------------------------------------------------------
# Hybrid LLM-in-loop path (hermetic, critic-gated)
# ---------------------------------------------------------------------------

def test_llm_augmenter_outputs_are_critic_gated():
    """Inject a generator that returns one good + one mis-booking paraphrase.
    The good one is kept; the bad one is dropped; safety stays intact."""
    def fake_llm(prompt: str) -> list[str]:
        if "2027-12-10 14:00" in prompt and "KEEP" in prompt:
            return ["could I grab 2027-12-10 at 14:00",      # good: same slot
                    "book 2027-12-10 09:00"]                 # bad: wrong slot
        return []

    pool = generate_pool(llm_generate_fn=fake_llm)
    from research.data_pipeline.nlu_eval import evaluate
    assert evaluate(normalize, pool)["unsafe_total"] == 0
    assert any("llm" in e.notes for e in pool), "a good LLM paraphrase should survive"
    # the wrong-slot paraphrase must not have been kept
    assert not any(e.utterance == "book 2027-12-10 09:00" and "llm" in e.notes
                   for e in pool)
