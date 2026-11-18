# R3/R4/R5 results: DEV split only (2026-11-18)

**Scope:** desktop `power` (RTX 5070 Ti, WSL, torch 2.15.0+cu132, transformers 5.23.0, peft 0.21.3, Python 3.12.16).
Dev = `dev.jsonl`, seed 20261118, n=600, families disjoint from train (sha256 in `docs/dataset_manifests/HASHES.md`).
**Not proven:** no sealed-holdout score, no Pi measurement. Reviews are same-owner (not independent).
Decoding: greedy, max_new_tokens=160, bf16. Prompt sha256 `cd8253a02311…` (zero-shot/LoRA) and `b3046857b317…` (7-shot).
Safety columns count violations: `inj_action` = action tool on an injection input, `false_action` = action tool where the oracle
expected something else, `wrong_args` = right action tool with wrong arguments, `claim` = text claiming success.

## R3 baselines (no tuning)
| model@rev | shots | schema | exact | inj_action | false_action | wrong_args | claim | violations |
|---|---|---|---|---|---|---|---|---|
| Qwen2.5-0.5B-Instruct@7ae55760 | 0 | 0.238 | 0.020 | 0 | 52 | 27 | 0 | 79 |
| Qwen2.5-0.5B-Instruct@7ae55760 | 7 | 0.778 | 0.153 | 0 | 111 | 52 | 0 | 163 |
| Qwen3-0.6B@c1899de2 (no thinking) | 0 | 0.843 | 0.315 | 0 | 38 | 20 | 0 | 58 |
| Qwen3-0.6B@c1899de2 (no thinking) | 7 | 0.987 | 0.348 | 0 | 40 | 23 | 0 | 63 |
| Qwen2.5-1.5B-Instruct@989aa798 | 0 | 0.678 | 0.207 | 0 | 31 | 12 | 0 | 43 |
| Qwen2.5-1.5B-Instruct@989aa798 | 7 | 0.907 | 0.355 | 0 | 78 | 25 | 0 | 103 |

## R4 equal-token-budget LoRA ablation (Qwen2.5-0.5B-Instruct@7ae55760)
Same hyperparameters for both arms: LoRA r=16, alpha=32, dropout=0.05, all attention+MLP projections, lr 2e-4 cosine,
5% warmup, 2 epochs, effective batch 16 (micro 2), bf16, budget 2,000,000 sequence tokens per epoch, seeds 0/1/2.
(a) `narrow` = random templates, clean canonical phrasing, no edge cases; (b) `diverse` = factor-balanced pairwise coverage 1.0.

| run | examples | seq tokens/epoch | supervised tok/epoch | schema | exact | inj_action | false_action | wrong_args | claim | violations | adapter sha256 (16) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| narrow s0 | 1362 | 1,999,569 | 68,080 | 0.665 | 0.443 | 0 | 34 | 17 | 0 | 51 | ba6d39b2808256b9 |
| narrow s1 | 1362 | 1,999,236 | 67,652 | 0.687 | 0.458 | 0 | 35 | 22 | 0 | 57 | 6553ea994163d732 |
| narrow s2 | 1362 | 1,999,975 | 67,956 | 0.607 | 0.377 | 0 | 36 | 24 | 0 | 60 | 8e1338afc5b71721 |
| diverse s0 | 1374 | 1,999,146 | 54,027 | 0.998 | 0.608 | 0 | 15 | 3 | 0 | 18 | 38a558864729baf7 |
| **diverse s1** | 1376 | 1,999,924 | 53,265 | 1.000 | **0.662** | 0 | 0 | 0 | 0 | **0** | d995a5125929828a |
| diverse s2 | 1373 | 1,999,006 | 54,413 | 1.000 | 0.600 | 0 | 5 | 1 | 0 | 6 | ecb2a6146e7110b4 |

Mean exact: narrow 0.426, diverse 0.623 (+0.197, all 3 diverse seeds above all 3 narrow seeds). Mean violations: 56 vs 8.
Peak VRAM 8.8 GB. Wall time varied 9–52 min per run because the owner's desktop apps used most of the VRAM.

### Honest reading
- The diverse arm wins on dev, but dev is drawn from the diverse distribution (edge cases, corrections, clarifications).
  The narrow arm never saw a clarification label, so part of the gap is expected by construction. That is evidence for
  H4 on *this* dev distribution, not a general proof.
- **Neither arm books correctly.** Diverse s1 predicts `ask_clarification` for all 61 dev cases whose oracle is
  `book_appointment` and all 27 `reschedule_appointment` cases. It is safe (0 false actions) but not yet useful for booking.
  Its `book`/`reschedule` exact scores come from cases where clarification was the right label. Likely cause: relative-date
  arithmetic is hard for a 0.5B model. Next step: let deterministic code resolve dates. The model would extract the
  phrase ("this Friday"), and code would turn it into a date (contract proposal).
- Injection is never actioned (inj_action 0 in every run), but diverse models often answer injection with
  `ask_clarification` instead of `handoff_to_human` (injection exact 0.12–0.57).

## R5 export (best dev run: diverse s1)
Merged with `lab/desktop/merge_adapter.py`, converted with llama.cpp `convert_hf_to_gguf.py` @ 398f00d65769e73bb5cad57c809429b160f2bb0e,
and quantized with the official llama.cpp release b12502 `llama-quantize` (CPU x64). Files are in `~/pi-model-lab/gguf/` on the desktop (WSL user `lex`):

| file | bytes | sha256 |
|---|---|---|
| qwen25-05b-receptionist-diverse-s1-Q4_K_M.gguf | 397,807,712 | d92c74d61e7eacfa810483f9c3a9fe880e1a59646bd1dd3c459afeac8018a9ce |
| qwen25-05b-receptionist-diverse-s1-Q8_0.gguf | 531,068,000 | f558dc7e66f9bb579109d7de06afdae5a70c60bf6ec2311d073deb347a34c602 |
| qwen25-05b-receptionist-diverse-s1-f16.gguf | 994,156,640 | 7bbee224b5220fcc4245b7e2cfd5a57d11c6222c94ba5c281dd5340cd78bf965 |

GGUF smoke test (desktop CPU, `lab/desktop/gguf_smoke.py`, first 40 dev prompts): Q4_K_M schema 1.000 / exact 0.650 / 0 violations;
Q8_0 schema 1.000 / exact 0.675 / 0 violations. This shows the conversion works. It is not a Pi benchmark.
