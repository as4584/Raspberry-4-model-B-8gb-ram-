# Sprint V — Zero-API-key Pi 4 voice testing with StreamDouble
Version 1.0 • Added from user's Google Doc tab **models + testing**, section “Pi 4 Voice Testing — Zero-API-Key Research Policy” • 2026-11-17
**Primary owner:** Laptop 2 Ramona (independent QA, experiment controller, model/data scientist)
**Dependency owner:** Laptop 1 Alex (Pi 4 + Tailscale/SSH + application WebSocket, ASR/TTS/LLM)
**Status:** PLANNED. No StreamDouble install/integration or actual Pi latency is verified. This plan does not authorize paid AI APIs, public test endpoints, customer calls, or live data.

## Operating principles / what exactly was requested
Production inference is LOCAL on the Raspberry Pi 4 Model B / 8 GB. Development agent uses a subscription coding tool only within plan limits—NOT pay-as-you-go API credits. A frontier agent plans/summarizes experiment batches and diagnoses meaningful failures; **it does not participate in any simulated call or grade every sample through an API**.
Circuit: Laptop 2 experiment controller → StreamDouble local Twilio-like caller → SSH-forwarded private WS endpoint on Pi 4 → on-Pi receptionist/ASR/LLM/TTS + SQLite test calendar → local metrics and database → compact sanitized summary → subscribed coding agent.
A local simulator does not establish real Twilio signing, PSTN cost, end-user telephone quality, or launch readiness. All experiments target a disposable test database; never real appointments.
Updated performance goal from tab: **p95 < 1000 ms between caller end-of-speech and first USEFUL audible reply**, measured with an independently defined end-of-speech and audio usefulness oracle, >=30 valid runs per stable config. Existing 1500ms aspirational figure is superseded for the final target; report both when historical comparisons are desired. StreamDouble's "time to first audio" alone may have a DIFFERENT clock origin and counts silent frames; not sufficient to prove this goal. For first valid reply require non-silent, intelligible response about the right booking state, never MockTTS silence or dummy tone.

## Source verification: technical feasibility / risk
- Package: https://pypi.org/project/streamdouble/ ; current reviewed release on PyPI as of 2026-11-17: **0.2.0**, Apache-2.0, Python >=3.11, alpha/pre-1.0 API. Pin that exact release in QA-only test dependencies **after isolated smoke** and record wheel SHA-256. Do not add to production runtime or run third-party installer scripts without inspection.
- Source: https://github.com/shaswat28/streamdouble ; published quickstart CLI includes `streamdouble call ws://localhost:8000/media-stream --audio hello.wav --out reply.wav`. **This project's endpoint is /twilio/media, not /media-stream**. Use exact project endpoint.
- StreamDouble itself checks transport timing, packet pacing, jitter, mark/clear and traces; it **does not** validate booking accuracy, natural conversation, or ASR quality. Must compose with independent oracle/test DB checks and consented speech fixtures.
- Publisher explicitly documents repetitions as **SEQUENTIAL, not parallel**. To test **two simultaneous callers**, Ramona must separately write an independent async multi-client WebSocket harness (e.g. pytest+websockets/httpx) with distinct callSid and streamSid. A sequential StreamDouble loop is NOT a concurrent call test.
- Twilio reference: https://www.twilio.com/docs/voice/media-streams/websocket-messages . One bidirectional <Connect><Stream> WSS per call; inbound media and outbound media/mark/clear on same socket, G.711 μ-law/8 kHz raw base64 audio, no WAV header. A parallel caller uses a second socket.
- Any actual model candidate benchmark remains subject to memory, cooling, version, license, quant and real Pi4 runtime support proof.

## Ordered deliverables and exact dependencies (do NOT try everything at once)
### V0 — Preflight/research, no Pi needed [PARALLEL with Alex S1; Ramona FIRST after S0]
RED tests: bad/missing Python version, wrong path `/media-stream`, fixture missing, unapproved external audio, mock silence accidentally categorized as audible reply. Validate license/version and docs; inspect package API/CLI and test in separate QA venv.
Deliver: docs/testing/streamdouble_compatibility.md with package exact version/sha, command, limitations, fixture manifest (sample rate, duration, source/license/consent), and explicit PASS/FAIL/UNKNOWN. A tiny test must connect to **the project's local /twilio/media** in dev-only mode. Test no Twilio keys or paid API invoked.
Gate V0: compatibility proof including WS connected/start/media/mark/stop and valid outbound protocol. Use mocks only for protocol, label MOCK evidence. Do NOT install on customer Pi unless independently required.
Human/owner action: none unless package installer access needs approval.

### V1 — Deterministic laptop-only transport harness [AFTER V0; CAN overlap Alex S2]
RED: corrupted base64, WAV headers in μ-law payload, wrong streamSid, missing or late mark, clear on caller interrupt, hangup while queued, silent agent yielding a false green score.
Create/commit: tests/streamdouble/ with fixture loader, local test WebSocket, `streamdouble` CLI adapter (arg contract locked/pinned), baseline raw JSON/JSONL and JUnit artifacts; separate deterministic DB-state oracle. Use synthetic speech-like waveform for codec protocol only; don't claim intelligibility.
Gate V1: 100+ deterministic pure transport/booking tests on laptop; show raw protocol evidence and no real calls. A mock's silence is a **FAIL** for "useful audible response" tests, PASS for payload framing tests.
Keep agent report ≤~1000 tokens; raw traces local under ignored test-results/, redact customParameters, tokens, numbers, voice and names.

### V2 — Remote Pi test execution over PRIVATE Tailscale [REQUIRES Alex S1 gate PASS]
StreamDouble runs on **Ramona's laptop**, not the Pi, whenever possible, avoiding simulator CPU load affecting Pi measurements. Pi's FastAPI server binds **127.0.0.1 only** in a safe disposable test environment; laptop creates:
```bash
ssh -N -L 18000:127.0.0.1:8000 <AUTHORIZED_PI_USER>@lex-receptionist-pi
```
Then separate terminal:
```bash
streamdouble call ws://127.0.0.1:18000/twilio/media --audio fixtures/consented_test_speech.wav --out test-results/pi-reply.wav --trace test-results/pi-trace.jsonl --json
```
**Example only after path/CLI preflight validates actual options and all fixtures exist.** On Windows use compatible PowerShell quoting and directories. `DEV_ALLOW_UNSIGNED=1` is permissible only on isolated Pi loopback with a controlled SSH tunnel; never public/Tailscale bound listener. Do not make app public to give StreamDouble access.
Use separately granted NONROOT credentials and an isolated Pi test user/checkout/db. Verify device model via /proc/device-tree/model, aarch64, ~8GB, host SHA, CPU temp, throttling and memory. Agent may not alter OS/Pi service without Alex authorization.
Gate V2: both laptops proved SSH per S1, a call simulated via Ramona's SSH tunnel, Pi hardware proof + host SHA + sanitized trace. Lack of physical Pi => BLOCKED, not passed.

### V3 — Genuine full-audio pipeline + independent score [REQUIRES S3/S4 ASR+LLM+TTS]
RED: silent MockTTS erroneously passes; ASR fails to transcribe fixture; agent confirms booking before DB commit; p95 measured from socket start instead of end-of-utterance; model creates false calendar writes.
Use real transcribable, properly licensed/consented speech fixtures; reference transcript, calendar fixture, expected event sequence/DB records and approved spoken-reply criteria. Voice tests have: (1) booking DB oracle, (2) transport/audio frames and interruptions, (3) full speech understanding + useful reply. Maintain reproducible mono PCM fixtures and hashes; do not record real customers.
Measure per event on Pi and test laptop with monotonic clocks or compatible timing offsets. Define exact speech endpoint, first playable outbound frame and first acoustically useful non-silent speech; mark audio confidence/inspection. StreamDouble's first-audio metric = supporting transport signal only, never the sole KPI.
Gate V3: at least 30 reproducible real local inference utterances, p50/p95 measured, accuracy and false-success count; quality judged against frozen expected answer, not just nonzero decibels. Goal <1000 ms p95 is **target**, may FAIL honestly.

### V4 — Reliability/chaos + true dual-stream concurrency [REQUIRES V1, S5 protocol integration]
StreamDouble network impairments: jitter 0/40/100ms, packet loss 0/1/5%, latency 0/120ms, explicit seeds and same WAV hash, interruption/hangup/silence; keep controlled baseline configs comparable.
**Separate async two-client harness** opens 2 simultaneous WS calls, distinct stream IDs and test callers; force same-slot race; exactly one booking success; disconnect one caller; verify other unaffected; bounded audio queues. Do not count sequential StreamDouble `-n` as concurrency.
Track empty responses, late mark echoes after clear, stale audio, buffer growth, crashes, temp excursions and false confirmations. Unchanged simulator input/hash/seed for comparable runs; record failed runs (no "retry until green").
Gate V4: no critical protocol/cross-talk/double-booking failures, bounded memory, no unsafe confirmations across fixed stress matrix. **V4 cannot verify signed live Twilio.**

### V5 — Bounded autonomous experiment batches & model/config tuning [REQUIRES S6 hardware baseline, V3]
Agent creates batch plan only. Experiment runner executes on approved Pi test environment **without invoking cloud/frontier inference**: model/quant verified short list, 1–4 CPU threads, batch/microbatch, prompt/context size, ASR chunk/VAD windows, TTS buffer sizes; log every trial and failures with config+model+binary SHA.
Experimental controls: one-variable-at-a-time diagnostics before factorial/Optuna combinations, 3 warmups, >=30 valid stable shots/config, temp/swap/throttling recorded, safe iteration budget + timeout. Do not optimize audio latency by cheating on correctness, by silently changing test load, or by skipping unresponsive trials.
Local Python/pytest computes p50/p95, independent full-audio response correctness and confidence intervals / valid-n; only then feed a ~1000-token report to subscription agent for recommendations. Raw payloads, private recordings and full logs must stay local by default. The AI agent should not become the per-call judge.
Gate V5: evidence-ranked recommendations, Pareto speed/safety tradeoff, honest <1000ms PASS/FAIL, no paid API keys. If Pi physically can't achieve target, document bottleneck + tradeoffs rather than invent throughput.

### V6 — Real Twilio cross-check [S7, WAIT FOR CREDENTIALS]
No StreamDouble test replaces an actual phone call. Requires owner's Twilio credentials securely configured, signed HTTPS/WSS ingress, spend limits, safe recordings/consent, 2 actual phone callers. Reproduce hardest simulator scenarios, compare transport drift and real call audio. G7 remains BLOCKED until this gate runs.

## Timeline / integration with BOTH existing workstreams
**Now while Alex works S1 Pi/Tailscale:** Ramona R0 model registry/legal shortlist (small bounded first pass) + V0 StreamDouble validation + R1 source licensing; Alex makes Git clone/venv/SSH and stable mock WS endpoint on Pi.
**Next before model or speech is production-ready:** Ramona V1 transport+booking oracle in laptop-only CI and R2 diverse datasets; Alex S2 deterministic booking+identity safety. Merge only reviewed passing tests.
**Immediately once S1 passes:** Ramona V2 SSH-tunneled protocol test on the actual Pi; Alex continues S3 on-device NLU and S4 speech.
**Once full audio works (S3/S4):** Ramona V3 complete call evaluation, V4 separate concurrent-call harness; Alex fixes failures, prioritizes safety.
**Only after above:** Ramona V5 parameter sweep and comparative data/model research on real Pi; Alex owns approved runtime changes. Then V6 when Twilio keys arrive.
If Ramona's temporary subscription runs out: preserve fixtures, tests, datasets, commands, PR links and next work in repo—local automated tests remain reusable with NO tokens; neither agent promises to continue unattended.

## TDD locks / reporting
Every V* PR: state role/branch/SHA, exact RED test and correct failure reason, GREEN same test + full suite, raw fixture hashes and sanitized output, stream version, environment LAPTOP / PI4 / MOCK / LIVE, clock origin, trials n, failed/invalid trials, whether result is audible and useful, CI link or explicitly UNVERIFIED. Never report 9/9 starter tests as V2–V5 completion. Keep high priority gates LOCKED on any invalid confirmation, no response, cross-call leak, nonreproducible metrics or missing signature on public endpoint.
Outputs: `docs/testing/streamdouble_compatibility.md` (written after actual preflight), `tests/streamdouble/`, `fixtures/approved_speech/` (respect licensing), `scripts/voice_eval.py`, `scripts/dual_call_harness.py`, `reports/voice/` with sanitized aggregate evidence, `.gitignore` for test-results/ and raw recordings. These paths are PLANNED; not claimed to exist. Fixtures may be external if license prohibits redistribution.

## Copy/paste assignment for Ramona agent
"You are Laptop 2 Ramona. Read AGENTS.md, docs/MASTER_PLAN.md, docs/CURRENT_STATE.md, docs/roles/laptop-2-ramona.md, docs/MODEL_DATA_LAB.md, and docs/STREAMDOUBLE_TEST_PLAN.md. The user added the 'Pi 4 Voice Testing — Zero-API-Key Research Policy' tab and delegates it to you. Preserve subscription-only frontier coding agent usage and no paid AI API keys. Start V0 by verifying StreamDouble 0.2.0 compatible with our /twilio/media WS, then V1 deterministic local protocol+booking tests. Only run V2 when Alex's Pi/Tailscale S1 is actually green; full audible V3/V5 wait for Alex's real local speech/LLM. Keep model/data work R0-R2 moving in parallel. Use separate true dual-socket harness for simultaneous calls; StreamDouble sequential -n is NOT concurrency. Work on test/evaluation-harness; RED→GREEN with evidence, do not mark live Pi or Twilio gates passed on mocks."
