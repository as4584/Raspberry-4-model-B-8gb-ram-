# S0 Gate Evidence — Baseline Test Suite

**Agent:** Ramona (Laptop 2)
**Branch:** test/evaluation-harness
**Date:** 2026-11-17
**Environment:** LAPTOP (darwin, x86_64/arm64), MOCK backend

## Gate: G0 — Local CI Green

### Command

```
PYTHONPATH=/Users/monabona/projects/raspi-receptionist python3 -m pytest tests/ -v
```

### Output

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
cachedir: .pytest_cache
rootdir: /Users/monabona/projects/raspi-receptionist
configfile: pyproject.toml
plugins: anyio-4.12.1
collected 9 items

tests/test_booking.py::test_booking_idempotency_and_overlap PASSED       [ 11%]
tests/test_booking.py::test_slot_race_two_connections PASSED             [ 22%]
tests/test_booking.py::test_no_confirmation_no_booking_and_dst PASSED    [ 33%]
tests/test_booking.py::test_confirm_twice_not_duplicate PASSED           [ 44%]
tests/test_media.py::test_health_simulator_twiML PASSED                  [ 55%]
tests/test_media.py::test_twilio_duplex_media_and_mark PASSED            [ 66%]
tests/test_media.py::test_codec_boundary PASSED                          [ 77%]
tests/test_media.py::test_two_calls_separate_and_same_slot_races PASSED  [ 88%]
tests/test_media.py::test_prod_webhook_requires_signature PASSED         [100%]

============================== 9 passed in 0.68s
```

## Metadata

| Field | Value |
|-------|-------|
| Git SHA | 7aeecb8a6d6884783122e02380e79c761d527c41 |
| Branch | test/evaluation-harness |
| Python | 3.9.6 (system) |
| pytest | 8.4.2 |
| Test count | 9 |
| Skips | 0 |
| Failures | 0 |
| Environment | LAPTOP / MOCK |
| Pi tested | NO |
| Twilio live | NO |

## Notes

- pyproject.toml declares `requires-python = ">=3.11"` but code is compatible with 3.9 for local testing.
- **Blocker:** GitHub Actions CI status unverified on this branch. G0 requires GitHub Actions green on Python 3.11/3.12.
- `zoneinfo`, `str.removeprefix`, `asyncio.to_thread` all available in Python 3.9; no 3.11-only syntax in source.
- Editable install (`pip install -e .`) fails on pip 21 (macOS system); workaround is `PYTHONPATH=<repo root>`.

## G0 Status

**PARTIAL** — local tests green, GitHub Actions not yet verified on this branch.
