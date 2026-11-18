# Project state / baton pass — updated 2026-11-18 (Laptop 1)

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
| S0 | Project instructions + branch/CI baseline | Alex + Laptop 2 Desktop | PASS (same-owner) | CI on 3.11/3.12/3.13, coverage >=80%, shellcheck, mutation >=0.80; main protected (5 required checks, admins included). Ramona left 2026-11-17: Laptop 2 = owner desktop, so review is not independent |
| S1 | Raspberry Pi + Tailscale remote development | Alex + Laptop 2 Desktop | NEARLY PASS: tailnet ACL not applied | Pi 4 Model B Rev 1.5 / 7.99 GB preflight OK; SSH over Tailscale from lex + power (non-root laptop2); Pi pytest; /healthz via tunnel; controlled + unplanned reboot recovered (#2). Open: least-privilege tailnet ACL + negative test; PSU under-voltage (0x50005) |
| S2 | Atomic booking + identity safety | Alex + agent | PARTIAL, tests green | #7 #16 #17: per-session isolation, atomic reschedule, business hours, caller verification wired to Twilio From, DST, 1000-op oracle battery, 8-process race; 276 pass on Pi. Open: stronger-than-caller-ID verification (owner decision) |
| S3 | Local LLM and FAQ/JSON correctness | Alex + model-lab agent | IN PROGRESS | Model lab agent (feat/model-lab): registry, tool schema, data generator, eval; llama.cpp + Qwen2.5-0.5B smoke building on Pi. No held-out evaluation yet |
| S4 | On-device ASR/VAD/TTS | Alex + agent | PARTIAL | #23: whisper.cpp v1.9.5 + Piper run on Pi; spoken fixture round-trips; Piper adapter bug fixed. #25: encoder window sized to utterance, 13.4 s -> 3.0 s (throttled A/B). All timings INVALID (under-voltage). Not wired into the service yet |
| S5 | Bidirectional Twilio-like WS + 2-call test | Alex | PARTIAL, strong | #14 malformed frames, #15 websockets dependency (Pi service returned 404 before) + event-loop freeze, #18 Pi soak 16,626 calls / 0 failures / no leak. Open: backpressure, live Twilio |
| S6 | Real Pi latency, memory and model optimization | Alex | BLOCKED by PSU under-voltage | Every Pi timing so far is invalid (CPU held at 600 MHz). Boot 71 s -> 30 s (#10) is valid |
| S7 | Twilio live call | Alex | BLOCKED pending credentials | Doppler CLI + token scripts on Pi; secret scan in CI; fail-closed signatures pinned (#13). Twilio account expected ~2 days |
| S8 | Operational security and recovery | Alex | PARTIAL, strong | Hardened systemd service (exposure 4.3, loopback, restart in ~5 s) #13; power-loss tests #11; nightly backup + restore drill #19; off-device copy to desktop #20; /readyz #21 #22. Open: real pull-the-plug test, log retention |
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
