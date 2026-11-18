# R2 — NLU Evaluation Harness & Diverse Benchmark

**Owner:** Ramona (Laptop 2) · **Date:** 2026-11-17 · **Env:** LAPTOP / MOCK / no API keys / no audio / no network

## Purpose

Maximize reliability of the natural-language understanding layer **before it
reaches the Pi**. The rule-based `receptionist.nlu.normalize` is a stand-in for
the local model (Qwen3-0.6B etc.) that will eventually run on-device. This
harness lets us grade *any* NLU implementation — today's rules, tomorrow's
model — against one frozen, labeled set, off-device, in milliseconds.

## Artifacts

| Artifact | Path |
|----------|------|
| Benchmark generator + evaluator | `research/data_pipeline/nlu_eval.py` |
| Frozen dataset (114 examples, JSONL) | `research/data/nlu_eval_set.jsonl` |
| Pytest gates | `tests/test_nlu_eval.py` (11 tests) |

Regenerate dataset + baseline report:

```bash
PYTHONPATH=. .venv311/bin/python3.11 -m research.data_pipeline.nlu_eval --report
```

## Dataset design

114 oracle-labeled examples. Oracle actions are **code-derived** (run through
the real `Dialogue` + `BookingStore`), not hand-annotated. Dimensions covered:

- **Language:** English (71), Spanish (41), code-switch/mixed (2)
- **Register:** formal, casual, keyword, punctuation-noisy, filler words,
  typo, spelled-out, relative, vague, compound, ASR-style errors
- **Intent:** book, affirm, deny, cancel, handoff, help, unknown/FAQ, vague, compound
- **Time:** exact 24h, 12h am/pm, DST spring-forward gap, DST fall-back fold,
  relative ("tomorrow", "next Monday"), spelled ("December tenth")

### Tiers

| Tier | Meaning | Rule-based contract |
|------|---------|---------------------|
| 1 | Canonical (structured commands, keyword yes/no/human/help, 12h clock) | command + action = **100%** |
| 2 | Natural (lead-in phrases, punctuation, filler, code-switch, DST) | command + action = **100%** |
| 3 | Model-required (relative/spelled/vague/compound/ASR) | **may miss**, but must **fail safe** |

## Safety invariant (all tiers, non-negotiable)

> The parser must never invent a booking, and never book the wrong slot. Any
> utterance it cannot understand must fall through to `help` — not a confirmed
> appointment.

The evaluator flags `unsafe` when a non-booking utterance yields a booking, or
a booking carries the wrong time. **The gate is `unsafe == 0`.** Two
"guard-the-guard" tests prove the detector actually fires (a normalizer that
books everything, and one that books the wrong time, are both caught).

## Rule-based baseline (2026-11-17)

```
total examples : 114
command acc    : 100.0%   (over graded examples)
action acc     : 92.1%
UNSAFE total   : 0        (gate = 0)   ✅

by tier:
  tier 1: n=65  command=100%  action=100%  book-extract=100%  unsafe=0
  tier 2: n=37  command=100%  action=100%  book-extract=100%  unsafe=0
  tier 3: n=12  command=n/a   action=25%   book-extract=0%    unsafe=0

by language:
  en   : n=71  action=92%
  es   : n=41  action=93%
  mixed: n=2   action=100%
```

**Reading it:** Tier 1 & 2 are fully solved by rules in both languages with
zero unsafe outcomes. Tier 3's low action accuracy (25%) and 0% booking
extraction are **expected and correct** — the rule-based layer has no ISO date
to latch onto for "book tomorrow at 2pm", so it safely routes to `help` every
time (0 unsafe). The 9 Tier-3 "failures" in the report are the model-required
items, not regressions.

## What this buys us for the Pi

1. **Tier 3 is the model's scorecard.** When the local model lands (S3A), these
   12 utterances flip from "fail safe to help" to "should book correctly." The
   target is to raise Tier-3 booking extraction from **0% → high** *while
   keeping `unsafe` at 0*. Same harness, same command, instant comparison.
2. **No-regression floor.** Any model or rule change is held to Tier 1/2 = 100%
   and `unsafe == 0` before it can ship toward the Pi.
3. **Cheap + deterministic.** Runs in ~0.4s with no hardware, no network, no
   keys — so it can gate every PR in CI long before Pi time is scarce.

## Reliability fixes surfaced by building the benchmark

Writing the diverse set exposed three real gaps in `normalize`, now fixed
(TDD, all covered by Tier 1/2 examples):

1. **Punctuation/phrase-tolerant keywords** — "yes please", "okay!", "no, thank
   you", "sí." now classify correctly (first-token + edge-punctuation match).
2. **Spanish "a las" connector in the English path** — "book 2027-12-10 a las
   14:00" (code-switch) now extracts instead of falling to help.
3. **Reference-less cancel → help (safety)** — "cancel my 2pm and book 3pm"
   previously reached the dialogue's loose `cancel ` handler and attempted a
   cancel on arbitrary text. The normalizer now routes any cancel without a
   valid reference to `help`, closing a latent unauthorized-cancel path.

A fourth guard prevents a filler affirmation from swallowing a real booking:
when an explicit ISO date is present ("ok so … 2027-12-10 at 9am"), the
utterance is treated as a booking, not a bare "yes".

## Open items / next

- Flip Tier 3 to scored once a model is available on the Pi (S3A).
- Expand Spanish Tier-2 register coverage (regional phrasings) as data lands.
- `project_synthetic` source still pending schema-audit owner sign-off before
  any example here is used for *training* (eval use is unrestricted,
  project-authored, no PII).
