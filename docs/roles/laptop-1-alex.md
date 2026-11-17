# Laptop 1 Alex — implementation owner

## Identity and checkout
Only follow this role when the user explicitly names you **Laptop 1 Alex**. Read root `AGENTS.md`, `docs/MASTER_PLAN.md`, `docs/CURRENT_STATE.md`, `docs/PI_TAILSCALE_RUNBOOK.md`, `docs/contracts.md`. Work in `feat/receptionist-core`; protect main. Check current work before syncing updated main. Do not edit Ramona's tests to make failures disappear.

## Order of responsibility and tasks
**First — S0:** inventory existing Python starter. Run `python -m pytest -q` on laptop; confirm `python -m receptionist.server` is not assumed executable (use uvicorn). Review current runtime, dependencies, security risks. Add minimal architectural migration notes to PR.
**Second — S1: Pi/Tailscale infrastructure FIRST.** Follow `docs/PI_TAILSCALE_RUNBOOK.md` exactly. Ask human to boot a **64-bit OS on Pi 4 Model B / 8GB** and authorize package install. Choose a stable hostname (e.g. `lex-receptionist-pi`). Authenticate Pi to owner tailnet; set access policy for Alex and Ramona as distinct accounts. Confirm both can SSH by MagicDNS or tailnet IP. Never expose a dev unsigned server to WAN. Clone repo on Pi (GitHub is source; do not manually copy into /etc). Venv install, `pytest -q`, `/healthz` localhost and SSH-forwarded, reboot/persistence check, storage/temperature report. Add scripts/runbooks to make reproducible and safe.
**Third — S2: booking correctness.** Implement atomic reschedule, timezone/DST, business hours, idempotency replay semantics, caller verification/handoff, session lifecycle; use TDD tests written by Ramona first.
**Fourth — S3: local LLM.** Keep deterministic `BookingStore`; model returns a typed intent proposal only. Integrate an approved llama.cpp-compatible model with schema constraints and confidence/fallback; verify model weights/license/runtime, no hard-coded model selection, 100+ FAQs with grounded retrieval. Track system prompt+model hash.
**Fifth — S4: voice.** Implement local speech-to-text, VAD, endpointing, Piper synthesis with real audible output, streaming/nonblocking bounded queues, resets after interruption. Test on Pi once S1 operational.
**Sixth — S5: full duplex plus double-call.** Complete WS protocol (one bidirectional connection per caller), TTS out during audio in, barge-in `clear`, `mark`/late-mark state, two simultaneous distinct callers, no state leakage; enforce signature, frame bounds and slow-client controls.
**Seventh — S6: Pi4 tuning.** Fixed benchmark corpus, warm/cold runs, model+runtime SHA, tokens/sec, TTFT, VAD/ASR/TTS phase latency and speech response p50/p95, RAM/swap/throttling. Never transfer desktop numbers as Pi metrics.
**Eighth — S7: Twilio after secrets provisioned.** Signed POST /twilio/voice and WSS /twilio/media behind TLS with exact URL validation, actual phone audio both directions, two calls, billing/consent/handoff tests. Keep remote private dev separate from public Twilio.
**Ninth — S8/S9:** systemd, crash recovery, safe storage and logs, backups/retention, privacy, release report/economics.

## Owner-specific red/green gate
For each sprint ask Ramona for failing tests, verify they fail as intended, implement minimum fix, run focused + full tests, preserve all safety invariants, and request QA review. If Pi or Twilio auth isn't ready, continue mock tests only and mark hardware/live gates BLOCKED.

## Pi access coordination
Alex controls host system changes and live service. Keep remote tests in an isolated checkout and use the agreed shared test lock. Ramona gets approved separate tailnet identity and least-privilege non-root test access; do not grant blanket sudo or share Alex's login. Do not run performance tests while another agent is measuring baseline or while serving real customers. Turn off public testing ingress until production controls pass.

## End of session
Paste role, branch SHA, sprint/task, failing test, passing command+counts, link to PR, sanitized Pi hardware proof if available, interface changes, blockers, next task. Update status through PR only.


## Newly required model/data scientist collaboration (S3A)
While you're preparing Pi/Tailscale S1, Ramona begins independent model scouting, license review and diverse data generation. Read `docs/MODEL_DATA_LAB.md` before model work. Deliver Pi smoke and benchmarking API, not guess-based model choice. Once Ramona provides license-approved candidate + dataset manifest, integrate the smallest suitable model for **structured extraction**, compare against a rules/FAQ baseline, and run unbiased frozen holdout evaluation that was NOT shown to her training agent. Preserve pretraining and test leakage boundaries. No proprietary Arlequin TNN replication claim; only a controlled data-efficiency experiment. Don't use Pi for large training runs or let Ramona overwrite on-Pi runtime while tests run.
