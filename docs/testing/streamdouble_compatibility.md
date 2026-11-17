# V0: StreamDouble Compatibility Preflight

**Agent:** Ramona (Laptop 2)
**Sprint:** V0 — Preflight Research
**Date:** 2026-11-17
**Status:** PARTIAL PASS — protocol compatible by static analysis; live connection proof blocked (Python 3.11 required)

---

## Package Identification

| Field | Value |
|-------|-------|
| Package name | `streamdouble` |
| Install command | `pip install streamdouble` |
| License | Apache-2.0 |
| GitHub | https://github.com/shaswat28/streamdouble |
| Python required | ≥ 3.11 |
| Twilio API key required | NO |
| Cloud inference required | NO |
| PyPI SHA | unverified (package not found on pip 21.2.4 / Python 3.9) |

---

## V0 Gate Requirements vs. Findings

| Requirement | Finding | Status |
|-------------|---------|--------|
| Apache-2.0 license | Confirmed in README and LICENSE | PASS |
| Python ≥ 3.11 | Confirmed; CI on 3.11, 3.12, 3.13 | PASS |
| Zero Twilio API keys | Confirmed: tool is fully local, no credentials | PASS |
| Compatible with `/twilio/media` endpoint | Accepts any URL — static protocol analysis confirms compat (see below) | PASS (static) |
| Protocol WS connection proof | Cannot run — Python 3.9 on this laptop | **BLOCKED** |
| Package SHA documented | Not recorded — cannot install | PENDING |
| Fixture manifest | Minimal fixtures defined (see below) | PARTIAL |

---

## Protocol Compatibility Analysis (Static)

StreamDouble implements the Twilio Media Streams v2 WebSocket protocol.

### Outbound events (StreamDouble → server)

| Event | StreamDouble sends | Our server.py expects | Compatible? |
|-------|-------------------|----------------------|------------|
| `connected` | `{event: "connected"}` | Handled (`continue`) | YES |
| `start` | `{event: "start", streamSid: "M1", start: {callSid: "C1", ...}}` | `evt['streamSid']`, `start['callSid']` | YES |
| `media` | `{event: "media", streamSid: "M1", media: {payload: "<base64>"}}` | Decoded via `base64.b64decode(payload)` | YES |
| `stop` | `{event: "stop"}` | Handled (`break`) | YES |
| `dtmf` | `{event: "dtmf", ...}` | Falls to `else: ws.close(1008)` | **INCOMPATIBILITY** |

**INCOMPATIBILITY FOUND:** StreamDouble can send `dtmf` events (documented in its outbound events). Our server.py closes the WebSocket with code 1008 on any unrecognized event. StreamDouble must be configured with `--no-dtmf` or equivalent, or our server must ignore unknown events gracefully.

**Recommended fix (Alex or Ramona):** Change server.py `else` clause to `continue` (ignore unknown events) rather than close, OR document that StreamDouble must not send dtmf events during V1 test runs. Closing the WebSocket on unknown events is fragile as the Twilio protocol may add fields.

### Inbound events (server → StreamDouble)

| Event | Our server sends | StreamDouble expects | Compatible? |
|-------|-----------------|---------------------|------------|
| `media` with μ-law payload | `{event: "media", streamSid: ..., media: {payload: ...}}` | Receives and measures latency | YES |
| `mark` | `{event: "mark", streamSid: ..., mark: {name: ...}}` | Mark echo matching | YES |
| `clear` | `{event: "clear", streamSid: ...}` | Recorded as turn reset | YES |

### Endpoint path
StreamDouble's examples use `/media-stream`, but it accepts any URL passed on the command line. Our endpoint `/twilio/media` is fully compatible:
```
streamdouble call ws://127.0.0.1:8000/twilio/media --audio fixtures/consented_test.wav
```

---

## Environment Blocker

**Blocker:** Python 3.9.6 installed on this laptop; StreamDouble requires Python ≥ 3.11.

```
$ python3 -m pip index versions streamdouble
ERROR: No matching distribution found for streamdouble
```

This is an environment issue, not a tool issue. pip 21.2.4 is also too old to resolve the package index correctly.

**Resolution path:**
1. Install Python 3.11+ on this laptop (via pyenv, Homebrew, or OS installer)
2. Create a venv: `python3.11 -m venv .venv && source .venv/bin/activate`
3. `pip install streamdouble`
4. Record installed version and SHA: `pip show streamdouble`
5. Run WS connection smoke test (see fixture manifest below)

---

## Minimal Fixture Manifest (V1 preparation)

Fixtures needed for V1 (can be prepared now, no audio required for V0):

| Fixture | Path | Content | Status |
|---------|------|---------|--------|
| Silence (20ms μ-law) | `tests/fixtures/silence_20ms.bin` | `bytes([255]*160)` | PENDING — write in V1 |
| "Book" intent mulaw | `tests/fixtures/book_intent_mock.bin` | MockASR transcript fixture | PENDING |
| Reference transcript | `tests/fixtures/transcripts/book_2027_12_01.txt` | `book 2027-12-01 14:00` | PENDING |
| Consented speech WAV | `tests/fixtures/consented_test.wav` | 8kHz mono μ-law consented audio | BLOCKED (requires real recorded consent) |

All audio fixtures must be:
- Consented (documented consent or synthetic)
- Mono PCM, 8 kHz μ-law (Twilio format)
- SHA256-hashed and documented

**Synthetic fixtures are speech-like but not transcribable** — per StreamDouble docs, they are "right for codec tests, [but] no STT will ever turn them into words." This is acceptable for V1 (transport testing); V3 requires real transcribable speech.

---

## dtmf Incompatibility — Issue Required

The dtmf incompatibility identified above must be tracked. Ramona will file a GitHub issue.

**Issue title:** `server.py closes WebSocket on unknown events (dtmf incompatibility with StreamDouble)`

**Impact:** StreamDouble sends `dtmf` events; our server responds with `ws.close(1008)`. This would abort test runs that include dtmf. Must be fixed before V1.

**Proposed fix:** In `server.py`, change:
```python
else: await ws.close(code=1008); break
```
to:
```python
else: continue  # ignore unknown Twilio protocol extensions
```
This is safer than closing, as future Twilio protocol versions may add new event types.

---

## V0 Gate Final Verdict

| Gate Criterion | Status |
|---------------|--------|
| Package Apache-2.0 confirmed | PASS |
| Zero Twilio keys | PASS |
| Protocol compatibility (static) | PASS with one fix needed (dtmf) |
| WS connection proof | **BLOCKED — Python 3.11 required** |
| Fixture manifest | PARTIAL |

**Overall V0 status: PARTIAL PASS**

V0 is blocked on Python 3.11 installation. Protocol compatibility confirmed by static analysis of server.py vs StreamDouble README. One incompatibility found (dtmf handling) requires a server.py fix before V1.
