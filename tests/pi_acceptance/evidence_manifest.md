# G1 Evidence Manifest

**Agent:** Ramona (Laptop 2)
**Gate:** G1 — Pi OS 64-bit, Tailscale dual SSH, remote pytest, private /healthz
**Status:** PENDING — Pi hardware not yet provisioned

This file is filled in once hardware is available. Each row maps to a checklist item in `checklist.md`.

---

## Hardware Identity

| ID | Command | Raw Output | Status |
|----|---------|-----------|--------|
| H1 | `cat /proc/cpuinfo \| grep Model` | _(unfilled)_ | PENDING |
| H2 | `uname -m` | _(unfilled)_ | PENDING |
| H3 | `free -h` | _(unfilled)_ | PENDING |
| H4 | `vcgencmd measure_temp` | _(unfilled)_ | PENDING |
| H5 | `vcgencmd get_throttled` | _(unfilled)_ | PENDING |
| H6 | `cat /etc/os-release` | _(unfilled)_ | PENDING |

---

## Tailscale Connectivity

| ID | Command | Raw Output | Status |
|----|---------|-----------|--------|
| T1 | `tailscale status` (Alex laptop) | _(unfilled)_ | PENDING |
| T2 | `tailscale status` (Ramona laptop) | _(unfilled)_ | PENDING |
| T3 | `tailscale ping <pi-ip>` (both) | _(unfilled)_ | PENDING |
| T4 | ACL policy | _(unfilled)_ | PENDING |
| T5 | `ssh pi@<tailscale-ip> echo ok` (both) | _(unfilled)_ | PENDING |

---

## Remote pytest

| ID | Command | Raw Output | Status |
|----|---------|-----------|--------|
| P1 | `ssh pi@... python3 --version` | _(unfilled)_ | PENDING |
| P2 | `ssh pi@... pytest tests/ -v` | _(unfilled)_ | PENDING |
| P3 | Platform string from pytest header | _(unfilled)_ | PENDING |
| P4 | Wall-clock time | _(unfilled)_ | PENDING |

---

## Health Endpoint

| ID | Command | Raw Output | Status |
|----|---------|-----------|--------|
| E1 | uvicorn startup | _(unfilled)_ | PENDING |
| E2 | SSH tunnel | _(unfilled)_ | PENDING |
| E3 | `curl http://127.0.0.1:18000/healthz` | _(unfilled)_ | PENDING |
| E4 | Direct Pi IP curl (must fail) | _(unfilled)_ | PENDING |

---

## Dual-Account Access Negatives

| ID | Test | Result | Status |
|----|------|--------|--------|
| N1 | Ramona SSH with Alex creds | _(unfilled)_ | PENDING |
| N2 | Alex SSH with Ramona creds | _(unfilled)_ | PENDING |
| N3 | `/simulate/text` unsigned in prod mode | _(unfilled)_ | PENDING |
| N4 | Cancel with fabricated UUID | _(unfilled)_ | PENDING |
| N5 | Cross-call session isolation | _(unfilled)_ | PENDING |

---

## No-Shared-Mutable-Tree

| ID | Command | Raw Output | Status |
|----|---------|-----------|--------|
| W1 | `ls -la` home dirs | _(unfilled)_ | PENDING |
| W2 | `ls -la /tmp/receptionist-*` | _(unfilled)_ | PENDING |

---

## G1 Final Verdict

**Status:** PENDING

_Fill all rows above with real output before changing status to PASS or FAIL._

**Completed by:** _(unfilled)_
**Date:** _(unfilled)_
**Git SHA at time of verification:** _(unfilled)_
