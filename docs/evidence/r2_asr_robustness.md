# R2 — ASR-Noise Robustness

**Owner:** Ramona (Laptop 2) · **Date:** 2026-11-17 · **Env:** LAPTOP / MOCK / no keys / no PII / no network

Callers reach the system through ASR, so the NLU layer is tested against
transcription noise. Motivated by AR-NLU (Interspeech 2024) and interventional-
noise SLU work; implemented as CheckList-style perturbations.

Files: `research/data_pipeline/asr_noise.py`, `tests/test_asr_robustness.py`.
Run: `PYTHONPATH=. .venv311/bin/python3.11 -c "from receptionist.nlu import normalize; from research.data_pipeline.asr_noise import run_robustness; print(run_robustness(normalize).as_dict())"`

## The invariant (gate)

> Under ANY ASR-style perturbation, the parser must **never book the wrong
> slot**, and a keyword must **never become a booking**. It may parse correctly
> or fail safe — never confidently book a time the caller didn't ask for.

Perturbations (deterministic): filler insertion, first-word disfluency/stutter,
uppercasing, trailing punctuation, dropped connectors, function-word homophones
("no"→"know", "yes"→"yeah"), and garbled time tokens ("2pm"→"2 p.m.").

## Rule-based baseline (2026-11-17)

```
booking perturbations : 42   unsafe = 0   ✅
  retained correct    : 39   (92.9%)
  failed safe         : 3    (garbled-time -> help, no booking)
keyword perturbations : 28   keyword_unsafe = 0   ✅
```

Zero wrong-slot bookings and zero keyword→booking conversions. Retention 92.9%
is the baseline the Pi model should raise (it should recover the garbled-time
cases too) while keeping unsafe at 0.

## Notes / scope

- ISO-digit corruption that yields a *different valid date* (e.g. ASR hears
  "the 11th" as "the 1st") is an **upstream ASR error, not a parser bug**; it is
  mitigated system-side by the dialogue's read-back confirmation, not here. This
  module perturbs phrasing/surface, not the ground-truth digits.
- A "guard-the-guard" test confirms a slot-blind parser is caught (it books the
  wrong time under noise → unsafe > 0).
