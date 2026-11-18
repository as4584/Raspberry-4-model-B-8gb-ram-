# Project state / baton pass — 2026-11-17

This is a **reviewed sprint status ledger**, not a live task tracker; update it in a PR as actual evidence arrives.

## Verified baseline
- GitHub repo exists and contains a **starter** receptionist. Existing files: `receptionist/server.py`, `receptionist/booking.py`, `receptionist/audio.py`, `receptionist/dialogue.py`, `tests/test_booking.py`, `tests/test_media.py`, `docs/contracts.md`, CI YAML.
- Previous controlled **local** pytest run reported **9/9 pass**. Re-run this on each laptop and in GitHub Actions before claiming either environment is green. CI status has not been verified here.
- Existing model understanding is a strict text-only baseline; `MockTTS` outputs silence, not intelligible speech. Live local ASR/TTS and small-model LLM accuracy are not validated.
- Existing Twilio WS is a **simulation-capable starter** and lacks demonstrated hardened public production deployment.
- Branches exist: `feat/receptionist-core` and `test/evaluation-harness`. Created before this plan; **merge the updated main into each branch before work**.
- Master specification lives in `docs/MASTER_PLAN.md`, Tailscale in `docs/PI_TAILSCALE_RUNBOOK.md`, role files under `docs/roles/`.

## Sprint board (only mark PASS with attached evidence)
| ID | Sprint | Owner | Status on 2026-11-17 | Unlock evidence |
|---|---|---|---|---|
| S0 | Project instructions + branch/CI baseline | Both | PARTIAL | Both laptops run tests, role bootstrap, CI green |
| S1 | Raspberry Pi + Tailscale remote development | Alex + Ramona QA | NOT STARTED | Pi 4/8GB preflight, own-account SSH from both laptops, Pi test run, private health check |
| S2 | Atomic booking + identity safety | Alex + Ramona QA | PARTIAL | Race/reschedule/DST/cancel tests green |
| S3 | Local LLM and FAQ/JSON correctness | Alex + Ramona QA | NOT STARTED | Held-out extraction report + safe fallback |
| S4 | On-device ASR/VAD/TTS | Alex + Ramona QA | NOT STARTED | Audible local speech test + codec/endpointing checks |
| S5 | Bidirectional Twilio-like WS + 2-call test | Alex + Ramona QA | PARTIAL | Protocol chaos tests + barge-in and caller isolation |
| S6 | Real Pi latency, memory and model optimization | Alex + Ramona QA | BLOCKED by S1/S3/S4 | Pi raw benchmark report and threshold verdict |
| S7 | Twilio live call | Alex + Ramona QA | BLOCKED pending credentials | Signed HTTPS/WSS live call + cost/consent gates |
| S8 | Operational security and recovery | Both | NOT STARTED | Security suite, safe upgrades, restarts/restore |
| S9 | Launch qualification and unit economics | Both | NOT STARTED | Acceptance report; demo/conditional/production verdict |

## First task to perform (both agents)
**Alex:** S0 verify updated main; start **S1 Pi/Tailscale runbook**, build safe preflight and install/remote deployment improvements. Pi hardware provisioning and auth steps require explicit owner interaction. Implement only on Alex branch.
**Ramona:** S0 verify updated main; write the **S1 independent test checklist and tests**, including Pi hardware identity, a remote-runner evidence manifest and dual-account access negatives. Then extend S2 booking/parallel calls tests. Do not block on Twilio.
**Shared integration order:** Once both PRs are reviewed, merge to main, then resync branches. Use separate directories or git worktrees if sharing a single laptop; do not use the same checkout for two agents.

## Session record template (copy into PR)
Role / branch / commit:
Sprint ID and blocker:
RED test: path + command + failure:
GREEN and full suite: command + pass/fail/skip:
Environment: local laptop / exact Pi hardware / mocked / live Twilio:
Evidence: CI run, raw JSONL, logs sanitized, hashes:
Review and merge status:
Next action and owner:

## External actions Alex must authorize
- Raspberry Pi OS and storage boot/SSH, local credentials, Tailscale installation/login.
- Invite Ramona's **own Tailscale identity** to the tailnet or share the Pi device with narrowly-scoped permissions. No credential sharing.
- Pi system package installation, service registration, public ingress, and adding real Twilio credentials.


## New parallel research task — USER PRIORITY
User explicitly requested one machine to discover tiny Pi models and build **complex heterogeneous training data**, experimentally testing whether a compact model can learn more per unit of compute. Assigned **Laptop 2 Ramona** as the primary Model & Data Lab researcher (S3A), while she retains independent test responsibility. Begin with **R0** models and license/compatibility shortlist, then **R1** source audit and factor taxonomy, then **R2** diverse synthetic generator with deterministic oracle and leakage guards. Research specification: `docs/MODEL_DATA_LAB.md`. Alex remains S1 Pi/Tailscale implementation owner and runs independent blind final evaluation. S3A is NOT STARTED. No candidate has yet been benchmarked on the user's Pi or trained for this project.

## New user-added execution lane — Zero-API-key Pi voice testing (2026-11-17)
The owner added a new “Pi 4 Voice Testing — Zero-API-Key Research Policy” under Google Doc `models + testing`. This is now **assigned to Laptop 2 Ramona**, alongside R0–R5 tiny-model/data research. Canonical sprint spec: `docs/STREAMDOUBLE_TEST_PLAN.md`. Current status: **V0 NOT STARTED; V1–V5 BLOCKED by their listed dependencies; V6 BLOCKED until Twilio credentials**. The tool itself has been verified as a published Python package (StreamDouble 0.2.0), but it has NOT been installed or tested in this project or on Pi.
Recommended next action for Ramona is **V0 compatibility smoke** on Laptop 2 and brief R0 model-source audit while Alex performs **S1 Pi + Tailscale**. V1 deterministic simulator runs can follow without hardware; V2 needs S1 PASS; V3 requires fully audible local ASR/LLM/TTS (S3/S4); V4 S5; V5 S6; V6 S7. Target **p95 < 1000 ms from end of caller speech to first useful audible response**, not simulator's raw first-audio packet metric. Only Pi physical runs can qualify.
No paid AI API keys, test logs local, frontier coding agent reports ~1000 tokens, no agent per-call inference/judging. StreamDouble repeated runs are sequential; true two-caller integration uses a separate independent concurrent WS harness.

## R2 Model & Data Lab — progress + handoff (Ramona, 2026-11-17, PR #6)
R1 scaffold extended through **R2**: bilingual rule-based NLU normalizer, a tiered oracle-labeled eval benchmark (114 ex, `unsafe==0` gate), quality metrics (Vendi + intent/slot/compositional coverage), CheckList behavioral tests (MFT/INV/DIR), and a **research-backed hybrid diversity engine** (seed→augment→critic→select) producing a dense grown dataset (240 of a 669 pool; coverage 100% intent/lang/register/slot; unsafe 0). Full suite **314 passed**. Research grounding in `docs/research/data_generation_best_practices.md`; evidence in `docs/evidence/r2_nlu_eval.md` and `docs/evidence/r2_diversity_engine.md`.
**Deferred for the next agent (no suitable hardware on Laptop 2 yet; Pi in setup) — see `docs/handoff/r2_diversity_engine_handoff.md`:** (1) install `sentence-transformers` → semantic Vendi (auto-activates, no code change); (2) wire `LLMAugmenter` to a local llama.cpp teacher (critic gates it; local-only); (3) at S3A flip grown-set tier-3 into the scored benchmark for a real model; (4) ASR-noise + LIMA/distillation + GBNF-constrained decoding. Invariant to preserve: critic drops any mis-booking → `unsafe==0`.
