# Handoff — R2 Diversity Engine & NLU Datasets

**From:** Ramona (Laptop 2) · **Date:** 2026-11-17 · **For:** whoever picks up R2/S3A next
**Branch / PR:** `test/conversation-sims-en-es` → PR #6

This captures everything built for the NLU data/eval work and the concrete next
steps that are **intentionally deferred** (local machine can't run model/embedding
workloads right now; Pi still being provisioned). Nothing here needs a GPU or
network to continue — the deferred items say exactly what to install/run when
hardware is ready.

## What exists (all committed on PR #6, all tests green: 314 passed)

| Area | Files |
|------|-------|
| Rule-based NLU normalizer (EN+ES, no LLM) | `receptionist/nlu.py` |
| Conversation sims (EN/ES, 181 tests) | `tests/test_nlu_normalizer.py`, `tests/test_conversations_en.py`, `tests/test_conversations_es.py` |
| Tiered eval benchmark (114 ex) + harness + safety gate | `research/data_pipeline/nlu_eval.py`, `research/data/nlu_eval_set.jsonl`, `tests/test_nlu_eval.py` |
| Quality metrics (Vendi + coverage) | `research/data_pipeline/metrics.py`, `tests/test_metrics.py` |
| Behavioral tests (CheckList MFT/INV/DIR) | `research/data_pipeline/behavioral.py`, `tests/test_behavioral.py` |
| Hybrid diversity engine | `research/data_pipeline/augment.py`, `research/data_pipeline/diversity_engine.py`, `tests/test_diversity_engine.py` |
| ASR-noise robustness (never mis-books under noise) | `research/data_pipeline/asr_noise.py`, `tests/test_asr_robustness.py`, `docs/evidence/r2_asr_robustness.md` |
| Grouped (leakage-safe) train/dev/holdout splits + contamination check | `research/data_pipeline/splits.py`, `tests/test_splits.py`, `research/data/splits/*.jsonl`, `docs/evidence/r2_splits.md` |
| Diagnostics: per-slot F1 + error breakdown; near-duplicate detection | `research/data_pipeline/slot_metrics.py`, `research/data_pipeline/dedup.py`, `tests/test_slot_metrics.py`, `tests/test_dedup.py`, `docs/evidence/r2_diagnostics.md` |
| Grown dataset (240 of 669 pool) | `research/data/nlu_grown_set.jsonl` |
| Research synthesis (cited) | `docs/research/data_generation_best_practices.md` |
| Evidence | `docs/evidence/r2_nlu_eval.md`, `docs/evidence/r2_diversity_engine.md` |

### How to run (laptop, no special hardware)

```bash
cd /Users/monabona/projects/raspi-receptionist
PYTHONPATH=. .venv311/bin/python3.11 -m pytest tests/ -q                 # full suite
PYTHONPATH=. .venv311/bin/python3.11 -m research.data_pipeline.nlu_eval --report
PYTHONPATH=. .venv311/bin/python3.11 -m research.data_pipeline.diversity_engine --report
```

## Non-negotiable invariants to preserve

1. **`unsafe_total == 0`** on every dataset (pool and selected). The engine's
   *critic* (`diversity_engine.critic`) re-parses every candidate — deterministic
   **or** LLM — and drops anything that would invent/mis-book. Do not weaken it.
2. **Never silently green a gate.** Tier 1/2 command+action = 100%; MFT/INV/DIR
   must pass; a slot-blind parser must still fail DIR.
3. **No PII, no secrets, no real audio** in any generated data.

## Deferred — ready for the next agent

### 1. Semantic Vendi Score (currently lexical)
The default embedder is lexical (word+char n-grams, `numpy` only) because this
machine has no torch. The code already supports a semantic upgrade with **zero
call-site changes**:

- `pip install sentence-transformers` into `.venv311`.
- `metrics.default_embedder()` auto-returns the multilingual backend
  (`paraphrase-multilingual-MiniLM-L12-v2`) via `metrics.semantic_embedder()`.
- Re-run the engine report; expect different (more semantic) Vendi numbers.
- Update `docs/evidence/r2_diversity_engine.md` with the semantic figures.
- **Do this before using the grown set to actually train a model.**

### 2. Wire the LLM-in-loop augmenter to a real local model
`augment.LLMAugmenter` takes an injected `generate_fn(prompt) -> list[str]`. It is
tested hermetically with a fake generator; it has **not** run against a real model
(none provisioned; cloud inference is against project policy).

- Implement a `generate_fn` that shells to local `llama.cpp` (or a local server)
  with a quantized teacher model.
- Pass it in: `generate_pool(llm_generate_fn=my_fn)`.
- The critic will validate/drop its outputs automatically — safe by construction.
- Keep it **local only**; do not add API keys to the repo.

### 3. Fold grown-set tier-3 into the scored benchmark (S3A, when a model exists)
Tier-3 items (`research/data/nlu_grown_set.jsonl`, relative/spelled/ASR) are the
model-required benchmark. Today the rule-based layer fails them safe (→help). When
a local model lands, these flip from "fail safe" to "should book correctly":
raise tier-3 booking extraction from 0% while keeping `unsafe == 0`. Harness is
`nlu_eval.evaluate(...)`; just point it at a real `normalize`/model adapter.

### 4. ASR-noise + fine-tuning data (per research doc §4)
Deferred to model phase: ASR-noise training pairs, LIMA-style curated +
distillation fine-tuning set, GBNF grammar-constrained command emission (safety),
reason-free-then-constrain. See `docs/research/data_generation_best_practices.md`.

## Open blockers (unchanged)

- Pi hardware provisioning (G1) — in progress.
- FunctionGemma Gemma ToU §3.2 sign-off; LFM2.5-350M Pi4 compat.
- `dtmf` server.py fix needs Alex review (earlier commit on this branch).
- `project_synthetic` / generated-data schema audit sign-off before any
  **training** use (eval use is fine: project-authored, no PII).

## Contact points in code
- Command vocabulary / safety state machine: `receptionist/dialogue.py`
- Factor taxonomy: `research/data_pipeline/schema.py`
- Engine entry points: `diversity_engine.generate_pool`, `select_diverse`,
  `quality_report`
