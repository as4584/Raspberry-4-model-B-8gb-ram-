# R2 — Hybrid Diversity Engine (grown NLU dataset)

**Owner:** Ramona (Laptop 2) · **Date:** 2026-11-17 · **Env:** LAPTOP / MOCK / no keys / no PII / no network

Implements the research-backed design in
[`docs/research/data_generation_best_practices.md`](../research/data_generation_best_practices.md).
Goal (per user): maximize **information density, semantic diversity,
compositional complexity, and generalization potential** of the dataset for a
small on-device model — not phrase count.

## Pipeline

```
SEED → AUGMENT → CRITIC → SELECT → REPORT
```

| Stage | What it does | Research basis |
|-------|--------------|----------------|
| **Seed** | Enumerate ground-truth (intent × slot-values × language) over the factor matrix; oracle action from the real `Dialogue`. | Schema/grammar backbone — SynTOD, M2M, Overnight |
| **Augment** | Over-generate surface variants from pluggable providers: deterministic rule-based realizers/perturbers + optional injected LLM. | AttrPrompt (factorized); two-stage label-preservation |
| **Critic** | Re-parse every candidate; keep label-preserving, keep parse-breaking-but-safe as tier 3, **drop anything that would mis-book**. | Generate-then-filter — Impossible Distillation, SuperGen |
| **Select** | Pick a subset maximizing diversity (farthest-point on embeddings) + coverage (new intent/slot/compositional cells) + language balance. | Vendi Score; coverage-driven selection |
| **Report** | Vendi, coverage, behavioral MFT/INV/DIR, zero-unsafe check. | CheckList; HELM multi-metric |

Files: `research/data_pipeline/augment.py`, `diversity_engine.py`,
`metrics.py`, `behavioral.py`. Output: `research/data/nlu_grown_set.jsonl`.

Run: `PYTHONPATH=. .venv311/bin/python3.11 -m research.data_pipeline.diversity_engine --report`

## The critic is the safety linchpin

Every candidate — deterministic **or** LLM — is re-parsed through the real
`normalize → Dialogue` and classified:

- **label-preserving** (booking resolves to the seed's exact slot) → kept, graded;
- **parse-breaking but safe** (routes to help/handoff, no booking) → kept as a
  tier-3 model-required benchmark item;
- **unsafe** (invents a booking, books the wrong slot, or a keyword turns into a
  booking) → **dropped**.

This makes the LLM-in-loop stage safe by construction: a noisy generator can
only *lose* candidates, never introduce a mis-booking. Verified by tests that
inject a mis-booking paraphrase and confirm it is dropped while a good one
survives, with `unsafe_total == 0`.

## Results (rule-based normalizer; deterministic providers)

```
pool → selected : 669 → 240
Vendi (pool)     : 32.91
Vendi (selected) : 36.51     ← effective distinct (selection raises density)
distinct-2       : 0.336
UNSAFE total     : 0         (gate = 0)   ✅

coverage (selected):
  intent        : 100%
  language       : 100%   en 108 / es 112 / mixed 20   (code-switch represented)
  register       : 100%   (formal/casual/keyword/punct/filler/casing/
                           code_switch/relative/spelled/asr/typo/vague/compound)
  slot buckets   : 100%   (morning/afternoon/evening/midnight, on-hour/half/
                           quarter, weekday/weekend, dst_edge, duration explicit)
  compositional  : 41 distinct (intent × lang × register) cells

behavioral       : 8/8 MFT · 20/20 INV · 5/5 DIR
```

**Density, not volume:** selecting 240 of 669 yields a *higher* effective
diversity ratio than the full pool (Vendi 36.5/240 vs 32.9/669) — the selection
discards near-duplicates and keeps the informative core. Target size is tunable
(`--target`).

**Note on the Vendi embedder:** the default local embedder is lexical
(word + char n-grams, dependency-free, multilingual). It rates
structurally-similar booking commands as similar, so the Vendi numbers are a
conservative lower bound on *semantic* diversity. A `sentence-transformers`
multilingual backend auto-activates if installed (`semantic_embedder()`), with
no call-site changes — recommended before using the set to train a model.

## Safety & quality gates (CI)

`tests/test_metrics.py` (11), `tests/test_behavioral.py` (5),
`tests/test_diversity_engine.py` (13). Enforced:

- `unsafe_total == 0` on both pool and selected set;
- critic drops keyword→booking and wrong-slot variants;
- MFT/INV/DIR all pass; a slot-blind parser fails DIR (regression guard);
- determinism (generation + selection reproducible);
- coverage 100% across intent/language/register/slot; language balance;
- selection increases density; ids unique; no PII.

Full suite: **314 passed** (was 285).

## What is intentionally deferred (S3A, model phase)

Per the research doc: grammar-constrained (GBNF) command emission;
reason-free-then-constrain; ASR-noise training pairs; LIMA-style curated +
distillation fine-tuning set. The engine already emits the tier-3
model-required items that become the model's scored benchmark, and the LLM
provider interface is ready to wire to a local llama.cpp teacher.

## Honest limitations

- Lexical (not semantic) default embedder — see note above.
- `compositional_coverage` %’s denominator is a capped envelope (120), since most
  raw factor products are nonsensical (e.g. handoff × spelled); the **distinct
  cell count (41)** is the meaningful figure.
- LLM-in-loop path is implemented and tested hermetically with an injected
  generator; it has **not** been run against a real model yet (no local model
  provisioned, no cloud inference per project policy).
