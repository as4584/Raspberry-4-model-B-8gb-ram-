# Raspberry Pi 4 Local AI Receptionist — executable foundation

[Engineering master specification (Google Doc)](https://docs.google.com/document/d/1pw6pMB9rPBjglflAK-l1i4pcpTMXr65xeBihhxzsJ6M/edit)

**Status: local text bookings, SQLite safety, and simulated Twilio WebSocket protocol only. Not production ready.** Live speech requires installing compatible local speech binaries and model files; live telephony requires Twilio credentials and a verified public WSS ingress. The 98% and 1.5s goals are not yet measured. Read the doc's `Shared architecture & acceptance`, `Laptop 1 Alex` and `Laptop 2 Ramona` tabs for sprint gates.

## Local test instructions

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate
pip install -e '.[dev]'
pytest -q
DEV_ALLOW_UNSIGNED=1 uvicorn receptionist.server:app --host 127.0.0.1 --port 8000
```

Smoke test a booking: `POST /simulate/text` with JSON `{"session_id":"demo","text":"book 2026-12-01 14:00"}` then another POST with `{"session_id":"demo","text":"yes"}`. Same session persists in the running server process. Appointment times are local `America/New_York`. Cancellation requires the exact returned opaque UUID: `cancel <booking-reference>`. Use `/healthz` to check process life, not full readiness.

## Twilio transport

`POST /twilio/voice` yields `<Connect><Stream>` pointed at `PUBLIC_WSS_URL`; WS `/twilio/media` handles start/media/mark/stop and sends media/mark/clear. Raw 8kHz G.711 μ-law is base64 encoded inside text JSON events. Barge-in clears buffered outbound media. The server has a simple RMS endpointing VAD, not a fully tuned speech-turn detector; benchmark and adjust it on Pi with consented audio. Tests inject mock ASR/TTS to avoid paid services.

**Security:** `DEV_ALLOW_UNSIGNED=1` disables Twilio signature checks and enables `/simulate/text`; use only on an isolated loopback/dev setup. Production MUST set `DEV_ALLOW_UNSIGNED=0`, `TWILIO_AUTH_TOKEN`, `PUBLIC_BASE_URL`, `PUBLIC_WSS_URL`, and a secure proxy that preserves the signed URL. Do not make an unsigned dev instance publicly reachable.

## Local speech adapters (unverified on your Pi)

Set `VOICE_BACKEND=local` and paths for `WHISPER_CLI`, `WHISPER_MODEL`, `PIPER_CLI`, `PIPER_MODEL`. This invokes whisper.cpp and Piper locally through subprocesses. Validate the exact CLI arguments against pinned versions before deploying. These are offline ASR/TTS adapters only; model-based conversation understanding, broad natural-language scheduling, human transfer and production-operational monitoring remain future sprints. Inference candidates in your doc: FunctionGemma 270M, LFM2.5 350M, Qwen3 0.6B; do not assume they share a valid GGUF runtime or comparable accuracy.

## Branch split

Alex owns `feat/receptionist-core` and runtime modules. Ramona owns `test/evaluation-harness`, fixtures, test gates and CI. Align interfaces before PRs. See `docs/contracts.md` for testable API. Neither account should commit real Twilio tokens, personal phone numbers, or call audio.

## Release status

G0 bootstrap: working locally after `pytest`; G1 booking: partial (concurrency and deterministic confirmation covered); G2 real LLM extraction: BLOCKED; G3 local speech: NOT VALIDATED ON PI; G4 simulated media protocol: partially tested; G5 Pi benchmarks: BLOCKED; G6 live Twilio: BLOCKED. Not yet a deployable 1.5-second fully local receptionist.


## Agent entry point (two laptops, one repository)
Start by telling either coding agent **"You are Laptop 1 Alex"** or **"You are Laptop 2 Ramona. Read AGENTS.md and begin the next unlocked sprint."** Each agent reads `AGENTS.md` → `docs/MASTER_PLAN.md` → `docs/CURRENT_STATE.md` → its `docs/roles/` file. Alex handles Pi Tailscale deployment and receptionist implementation. Ramona researches tiny models and **diversity-optimized training data** plus adversarial tests. The physical Pi must be set up by a human once; agents cannot establish Tailscale identity/auth without the user's authorized sign-in. See `docs/PI_TAILSCALE_RUNBOOK.md` and `docs/MODEL_DATA_LAB.md`.
