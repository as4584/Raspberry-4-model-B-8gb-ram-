# S1 Pi Hardware Acceptance Checklist

**Gate:** G1
**Owner:** Ramona (Laptop 2) — verification only; Alex provisions
**Status:** PENDING (Pi hardware not yet available)

Each item requires documented evidence in `evidence_manifest.md`.
No item may be self-certified by the provisioner alone.

---

## 1. Hardware Identity

| Check | Evidence Required | Pass Criteria |
|-------|------------------|---------------|
| H1 | `cat /proc/cpuinfo` output | Model name contains "Raspberry Pi 4" |
| H2 | `uname -m` output | `aarch64` |
| H3 | `free -h` output | Total RAM ≥ 7.5 GiB |
| H4 | `vcgencmd measure_temp` output | Temperature < 70°C at idle |
| H5 | `vcgencmd get_throttled` output | Returns `throttled=0x0` (no throttling) |
| H6 | OS image verified | 64-bit Raspberry Pi OS (Bookworm or later); `cat /etc/os-release` |

---

## 2. Tailscale Connectivity (Dual-Agent)

| Check | Evidence Required | Pass Criteria |
|-------|------------------|---------------|
| T1 | `tailscale status` from Alex's laptop | Pi appears as peer, Tailscale IP shown |
| T2 | `tailscale status` from Ramona's laptop | Pi appears as peer (independent auth account) |
| T3 | `tailscale ping <pi-ip>` from each laptop | Pong received, RTT < 500 ms |
| T4 | ACL screenshot or `tailscale policy` | Both laptops authorized to reach Pi port 22 |
| T5 | `ssh pi@<tailscale-ip> echo ok` from each laptop | Returns "ok" without password prompt |

---

## 3. Remote pytest Execution

| Check | Evidence Required | Pass Criteria |
|-------|------------------|---------------|
| P1 | `ssh pi@<tailscale-ip> 'python3 --version'` output | Python ≥ 3.11 |
| P2 | `ssh pi@<tailscale-ip> 'cd /home/pi/receptionist && python3 -m pytest tests/ -v'` | All baseline tests pass on Pi |
| P3 | pytest output includes `platform linux` and `aarch64` | Confirms execution is on Pi, not laptop |
| P4 | Total test time recorded | Baseline wall-clock time for future comparison |

---

## 4. Health Endpoint (Private)

| Check | Evidence Required | Pass Criteria |
|-------|------------------|---------------|
| E1 | FastAPI server started on Pi: `uvicorn receptionist.server:app --host 127.0.0.1 --port 8000` | No startup error |
| E2 | SSH tunnel established: `ssh -N -L 18000:127.0.0.1:8000 pi@<tailscale-ip>` | Tunnel open (no error) |
| E3 | `curl -s http://127.0.0.1:18000/healthz` from laptop | Returns `{"status":"ok","voice_backend":"mock"}` |
| E4 | Direct `curl http://<pi-tailscale-ip>:8000/healthz` **fails or is blocked** | Confirms server binds 127.0.0.1 only (not public) |

---

## 5. Dual-Account Access Negatives

| Check | Evidence Required | Pass Criteria |
|-------|------------------|---------------|
| N1 | Ramona SSH attempt to Pi using Alex's credentials | Access denied (permission verification, not an attack) |
| N2 | Alex SSH attempt to Pi using Ramona's credentials | Access denied |
| N3 | Curl to `/simulate/text` on Pi with `dev_allow_unsigned=false` | Returns HTTP 404 |
| N4 | Unauthorized cancellation: attempt to cancel a booking with a fabricated UUID | Returns "not found", DB unchanged |
| N5 | Cross-call session: use session_id from call 1 in call 2 | Dialogue state is independent (no leakage) |

---

## 6. No-Shared-Mutable-Tree Check

| Check | Evidence Required | Pass Criteria |
|-------|------------------|---------------|
| W1 | `ls -la /home/alex/receptionist /home/pi/receptionist` on Pi | Separate directories; different owners |
| W2 | `ls -la /tmp/receptionist-*` after each agent's test run | No shared tmp artifacts between agent runs |

---

## G1 Gate Summary

All 6 sections must be checked and evidence recorded in `evidence_manifest.md` before G1 is declared PASS. Any FAIL or PARTIAL item blocks S2 Pi-dependent work and must be reported as a GitHub Issue.
