"""R2 diversity engine — Ramona (Laptop 2).

Builds a dense, high-diversity, high-coverage NLU dataset by:

  1. SEED      — enumerate ground-truth (intent x slot-values x language) over
                 the project factor matrix; oracle action from the real Dialogue.
  2. AUGMENT   — over-generate surface variants from pluggable providers
                 (deterministic rule-based + optional injected LLM).
  3. CRITIC    — re-parse every candidate; keep label-preserving ones, keep
                 parse-breaking-but-SAFE ones as tier 3, DROP anything that would
                 mis-book. Safety is enforced here, not hoped for.
  4. SELECT    — pick a subset that MAXIMIZES diversity (farthest-point on
                 embeddings) and coverage (new intent/slot/compositional cells),
                 rather than maximizing phrase count. Information density > size.
  5. REPORT    — Vendi Score (before/after), coverage, behavioral MFT/INV/DIR,
                 and a zero-unsafe safety check.

Deterministic by default (no network, no keys, no PII). The LLM stage is opt-in
via an injected generate_fn and is always gated by the critic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from typing import Callable

import numpy as np

from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue
from receptionist.nlu import normalize as default_normalize
from research.data_pipeline.augment import (
    DeterministicAugmenter,
    LLMAugmenter,
    Seed,
    Variant,
)
from research.data_pipeline.behavioral import run_behavioral
from research.data_pipeline.metrics import (
    _slot_buckets,
    coverage_report,
    default_embedder,
    distinct_n,
    vendi_score,
)
from research.data_pipeline.nlu_eval import NLUExample, evaluate

import re

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}")
_SAFE_NONBOOK = {"help", "handoff", "reply", "not_found", "invalid"}
_BOOKING = {"confirm_required", "booked"}


# ---------------------------------------------------------------------------
# Seed matrix
# ---------------------------------------------------------------------------

_SAFE_DATES = ["2027-12-10", "2027-12-11", "2028-01-05", "2027-12-20"]  # Fri/Sat/Wed/Mon
_TIMES = ["09:00", "14:00", "18:30", "11:15", "16:00", "00:30"]
_DST = [("2027-03-14", "02:30"), ("2027-11-07", "01:30")]


def _oracle_action(command: str) -> str:
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        path = f.name
    d = Dialogue(BookingStore(path), timezone_name="America/New_York")
    return d.handle(command, "seed").action


def build_seeds() -> list[Seed]:
    seeds: list[Seed] = []
    # Bookings (safe + DST), both languages
    for lang in ("en", "es"):
        base = "book" if lang == "en" else "reservar"
        for date in _SAFE_DATES:
            for t in _TIMES:
                cmd = f"book {date} {t}"
                seeds.append(Seed("book", lang, f"{base} {date} {t}", f"{date} {t}",
                                  _oracle_action(cmd), cmd))
        # a couple of explicit-duration seeds to exercise the duration slot
        for dur in ("15", "60"):
            cmd = f"book {_SAFE_DATES[0]} 14:00 {dur}"
            seeds.append(Seed("book", lang, f"{base} {_SAFE_DATES[0]} 14:00 {dur}",
                              f"{_SAFE_DATES[0]} 14:00", _oracle_action(cmd), cmd))
        for date, t in _DST:
            cmd = f"book {date} {t}"
            seeds.append(Seed("book", lang, f"{base} {date} {t}", f"{date} {t}",
                              _oracle_action(cmd), cmd))
    # Keyword intents
    kw = {"affirm": ("yes", "reply"), "deny": ("no", "reply"),
          "handoff": ("human", "handoff"), "help": ("help", "help")}
    primary = {("affirm", "en"): "yes", ("affirm", "es"): "sí",
               ("deny", "en"): "no", ("deny", "es"): "no gracias",
               ("handoff", "en"): "operator", ("handoff", "es"): "operador",
               ("help", "en"): "help", ("help", "es"): "ayuda"}
    for (intent, (cmd, action)) in kw.items():
        for lang in ("en", "es"):
            seeds.append(Seed(intent, lang, primary[(intent, lang)], None, action, cmd))
    # Cancel
    ref = "00000000-0000-0000-0000-000000000000"
    seeds.append(Seed("cancel", "en", f"cancel {ref}", None, "not_found", f"cancel {ref}"))
    seeds.append(Seed("cancel", "es", f"cancelar {ref}", None, "not_found", f"cancel {ref}"))
    # FAQ / unknown
    for utt, lang in [("what are your hours", "en"), ("do you take insurance", "en"),
                      ("¿cuánto cuesta?", "es"), ("¿están abiertos los sábados?", "es")]:
        seeds.append(Seed("unknown", lang, utt, None, "help", None))
    # Vague / compound (tier-3 ideal = clarify/help)
    for utt, lang, intent in [("sometime next week would be great", "en", "vague"),
                              ("whenever you have an opening", "en", "vague"),
                              ("cancel my 2pm and book 3pm instead", "en", "compound")]:
        seeds.append(Seed(intent, lang, utt, None, "help", None))
    return seeds


# ---------------------------------------------------------------------------
# Critic — the safety linchpin
# ---------------------------------------------------------------------------

def _make_id(utterance: str, intent: str) -> str:
    h = hashlib.sha1(f"{intent}:{utterance}".encode("utf-8")).hexdigest()[:10]
    return f"gen-{h}"


def critic(seed: Seed, var: Variant, normalize_fn) -> NLUExample | None:
    """Return a labeled example if the variant is safe & meaningful, else None."""
    cmd = normalize_fn(var.utterance)
    action = _oracle_action(cmd)
    m = _ISO.search(cmd)
    slot = m.group(0) if m else None

    # code-switch variants are, by construction, mixed-language.
    lang = "mixed" if var.register == "code_switch" else seed.lang

    def ex(tier, expected_action, expected_command, expected_slot, note):
        return NLUExample(
            id=_make_id(var.utterance, seed.intent),
            utterance=var.utterance, lang=lang, register=var.register,
            intent=seed.intent, tier=tier, expected_action=expected_action,
            expected_command=expected_command, expected_slot=expected_slot,
            notes=note,
        )

    if seed.expected_action == "confirm_required":            # safe booking
        if action == "confirm_required" and slot == seed.slot:
            return ex(min(var.tier, 2), "confirm_required",
                      seed.grade_command if var.grades_command else None,
                      seed.slot, f"{var.source}/{var.register}")
        if action in _SAFE_NONBOOK and action not in _BOOKING:
            return ex(3, "confirm_required", None, None,
                      f"{var.source}/parse-breaking; fails safe")
        return None                                            # mis-book → DROP

    if seed.expected_action == "invalid":                     # DST booking
        if action == "invalid":
            return ex(min(var.tier, 2), "invalid",
                      seed.grade_command if var.grades_command else None,
                      seed.slot, f"{var.source}/dst")
        if action in _SAFE_NONBOOK and action not in _BOOKING:
            return ex(3, "invalid", None, None, f"{var.source}/dst fails safe")
        return None                                            # booked DST → DROP

    # keyword / faq / vague / compound — ideal is a specific non-booking action
    if action in _BOOKING:
        return None                                            # became a booking → DROP
    if action == seed.expected_action:
        return ex(min(var.tier, 2), seed.expected_action,
                  seed.grade_command if var.grades_command else None, None,
                  f"{var.source}/{var.register}")
    # safe but not the ideal action: keep only tier-3 ideal-labeled items
    if seed.intent in ("vague", "compound"):
        return ex(3, seed.expected_action, None, None, f"{var.source}/{seed.intent}")
    return None


# ---------------------------------------------------------------------------
# Generate (seed -> augment -> critic) with de-dup
# ---------------------------------------------------------------------------

def generate_pool(normalize_fn=None, llm_generate_fn: Callable | None = None
                  ) -> list[NLUExample]:
    normalize_fn = normalize_fn or default_normalize
    providers = [DeterministicAugmenter()]
    if llm_generate_fn is not None:
        providers.append(LLMAugmenter(llm_generate_fn))

    pool: list[NLUExample] = []
    seen: set[str] = set()
    for seed in build_seeds():
        for prov in providers:
            for var in prov.variants(seed):
                key = var.utterance.strip().lower()
                if key in seen:
                    continue
                labeled = critic(seed, var, normalize_fn)
                if labeled is None:
                    continue
                seen.add(key)
                pool.append(labeled)
    return pool


# ---------------------------------------------------------------------------
# Diversity + coverage maximizing selection
# ---------------------------------------------------------------------------

def select_diverse(pool: list[NLUExample], target: int, embedder=None
                   ) -> list[NLUExample]:
    if target >= len(pool):
        return list(pool)
    embedder = embedder or default_embedder()
    x = embedder.embed([e.utterance for e in pool])
    n = len(pool)
    cells = [(e.intent, e.lang, e.register) for e in pool]
    langs = [e.lang for e in pool]
    slots = [frozenset(_slot_buckets(e.expected_slot, e.utterance, e.intent))
             for e in pool]
    # Target language mix for a bilingual + code-switch training set.
    lang_quota = {"en": 0.45, "es": 0.45, "mixed": 0.10}

    selected: list[int] = []
    chosen = np.zeros(n, dtype=bool)
    max_sim = np.full(n, -1.0)
    covered_cells: set = set()
    covered_slots: set = set()
    lang_count: dict = {}

    # Mandatory backbone: every tier-1 canonical, so MFT capabilities survive.
    backbone = [i for i, e in enumerate(pool)
                if e.tier == 1 and pool[i].register in ("formal", "keyword")]
    order = backbone or [0]

    def choose(i: int):
        chosen[i] = True
        selected.append(i)
        covered_cells.add(cells[i])
        covered_slots.update(slots[i])
        lang_count[langs[i]] = lang_count.get(langs[i], 0) + 1
        np.maximum(max_sim, x @ x[i], out=max_sim)

    for i in order:
        if not chosen[i]:
            choose(i)
        if len(selected) >= target:
            return [pool[i] for i in selected]

    while len(selected) < target:
        score = 1.0 - max_sim                       # farthest-point term
        for i in range(n):
            if chosen[i]:
                score[i] = -np.inf
                continue
            if cells[i] not in covered_cells:
                score[i] += 0.6                     # new compositional cell
            if slots[i] - covered_slots:
                score[i] += 0.4                     # new slot bucket
            share = lang_count.get(langs[i], 0) / max(1, len(selected))
            if share < lang_quota.get(langs[i], 0.0):
                score[i] += 0.5                     # under-quota language
        j = int(np.argmax(score))
        if not np.isfinite(score[j]):
            break
        choose(j)
    return [pool[i] for i in selected]


# ---------------------------------------------------------------------------
# Quality report
# ---------------------------------------------------------------------------

def quality_report(pool: list[NLUExample], selected: list[NLUExample],
                   normalize_fn=None) -> dict:
    normalize_fn = normalize_fn or default_normalize
    emb = default_embedder()
    utts_pool = [e.utterance for e in pool]
    utts_sel = [e.utterance for e in selected]
    cov = coverage_report(selected)
    beh = run_behavioral(normalize_fn)
    safety = evaluate(normalize_fn, selected)
    return {
        "pool_size": len(pool),
        "selected_size": len(selected),
        "vendi_pool": round(vendi_score(utts_pool, emb), 2),
        "vendi_selected": round(vendi_score(utts_sel, emb), 2),
        "distinct_2": round(distinct_n(utts_sel, 2), 3),
        "coverage": cov.as_dict(),
        "behavioral": beh.as_dict(),
        "unsafe_total": safety["unsafe_total"],
        "by_tier": {t: {"n": m["n"], "unsafe": m["unsafe"]}
                    for t, m in safety["by_tier"].items()},
    }


def to_jsonl(examples: list[NLUExample]) -> str:
    return "\n".join(json.dumps(asdict(e), ensure_ascii=False) for e in examples)


def _format(report: dict) -> str:
    c = report["coverage"]
    return "\n".join([
        "Diversity engine — grown dataset",
        "=" * 34,
        f"pool -> selected : {report['pool_size']} -> {report['selected_size']}",
        f"Vendi (pool)     : {report['vendi_pool']}",
        f"Vendi (selected) : {report['vendi_selected']}   <- effective distinct",
        f"distinct-2       : {report['distinct_2']}",
        f"UNSAFE total     : {report['unsafe_total']}  (gate = 0)",
        "",
        "coverage (selected):",
        f"  intent        : {c['intent_coverage']:.0%}  missing={c['missing_intents']}",
        f"  language       : {c['lang_coverage']:.0%}  {c['lang_distribution']}",
        f"  register       : {c['register_coverage']:.0%}",
        f"  slot buckets   : {c['slot_bucket_coverage']:.0%}  missing={c['missing_slot_buckets']}",
        f"  compositional  : cells={c['compositional_cells']} cov={c['compositional_coverage']:.0%}",
        "",
        f"behavioral       : {report['behavioral']['mft']} MFT / "
        f"{report['behavioral']['inv']} INV / {report['behavioral']['dir']} DIR",
    ])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="research/data/nlu_grown_set.jsonl")
    parser.add_argument("--target", type=int, default=240)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    from pathlib import Path
    pool = generate_pool()
    selected = select_diverse(pool, args.target)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(to_jsonl(selected) + "\n", encoding="utf-8")
    print(f"Wrote {len(selected)} examples (from pool of {len(pool)}) to {args.out}")
    if args.report:
        print()
        print(_format(quality_report(pool, selected)))
