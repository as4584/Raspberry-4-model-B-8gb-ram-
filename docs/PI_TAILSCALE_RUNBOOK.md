# Raspberry Pi 4 — Tailscale, GitHub deploy, 2-laptop remote test runbook
Version 1.1 · 2026-11-17 · Sprint S1 · Owners Alex (infrastructure) / Ramona (independent verification)
Follow official docs: https://tailscale.com/docs/install/linux ; https://tailscale.com/docs/features/tailscale-ssh ; https://tailscale.com/docs/how-to/connect-to-devices
**Nothing in this file asserts that the user's Pi is already connected.** Commands marked PI run on physical Raspberry Pi OS terminal. Don't run an unreviewed internet pipe-to-shell installer, expose SSH to router WAN, or store auth tokens in git.

## 1. Prerequisites: do this before agents claim S1 pass
- Physical Raspberry Pi **4 Model B, 8 GB**, stable official-grade power supply, cooling, working SD/USB boot drive, active LAN/Wi-Fi, **64-bit Raspberry Pi OS**, local human access or existing local SSH.
- Alex and Ramona have separate GitHub identities; repository collaborator invitation accepted; each may clone. Each has its **own Tailscale account** authorized for the intended tailnet; Alex administers access policy. Don't share a VPN login, SSH keys or passwords.
- A Pi local Unix user chosen by Alex: `<PI_USER>`. Never assume Raspberry Pi OS defaults to username `pi`. Enable SSH deliberately with `sudo raspi-config` (Interfaces → SSH, if using stock sshd) or documented Raspberry Pi Imager option. With Tailscale SSH enabled, a Tailscale SSH ACL and valid OS user are required.
- Git installed; Python 3.11+ (check actual packaged version); `python3-venv`; `curl` for health. Python >=3.11 project requirement. Avoid `sudo pip`.

## 2. Raspberry Pi owner setup (on the Pi)
Run and record outputs, redacting personal tailnet data:
```bash
uname -m
cat /proc/device-tree/model; echo
free -h
cat /etc/os-release
python3 --version
sudo apt update
sudo apt install -y git python3-venv python3-pip curl
```
Expected: ARM64 `aarch64`, Raspberry Pi 4 Model B, approximately 8 GB RAM. If x86/32-bit/wrong Pi: **STOP**, no hardware claim.
Install Tailscale using the **official, reviewed package instructions for the detected Raspberry Pi OS release**: https://tailscale.com/docs/install/linux or https://pkgs.tailscale.com/stable/ . The one-line `curl ... | sh` exists in Tailscale docs but is *not* the chosen unattended path; inspect any remote installer before running. After install:
```bash
sudo tailscale up --hostname=lex-receptionist-pi
tailscale status
tailscale ip -4
```
A browser authentication URL may appear; the owner approves it. Do not store that URL or tailnet keys in the repository.
**SSH choice A (recommended when Tailscale policy is correctly configured):** Tailscale SSH on Pi:
```bash
sudo tailscale set --ssh
```
Requires BOTH tailnet network access rule and SSH access rule mapping Alex and Ramona's identities to explicitly allowed **non-root** OS user(s). Use `check` for high-risk access if desired. Enable this **only after policies are reviewed and an alternate local SSH/console path is working**: enabling Tailscale SSH can interrupt existing tailnet SSH sessions. Do NOT assume a default all-to-all policy is least privilege.
**SSH choice B:** keep conventional `sshd`, create unique OS user/key access for each developer, restrict access using Tailscale ACLs to Pi:22; do not advertise SSH to Internet or share a root password. Do not combine A and B casually; document choice in private operator notes.

## 3. Connect the two laptops (their own accounts)
Install Tailscale Windows/macOS client from https://tailscale.com/download . Sign in with each **separate, invited** identity to Alex's tailnet (or use a specifically shared Pi with correct grants). Verify both can see the Pi in the Tailscale admin/device UI and have appropriate ACL/SSH permission.
On each laptop (PowerShell or terminal):
```bash
tailscale status
tailscale ping lex-receptionist-pi
ssh <PI_USER>@lex-receptionist-pi
```
If MagicDNS resolution fails, use the Pi's actual `tailscale ip -4` output in `tailscale ping`/ssh; never assume a specific 100.x IP. First connection host key confirmation requires checking the destination identity. If connection fails: verify Pi online, matching tailnet/shared-device access, ACL for TCP22, correct SSH user, and chosen A/B server path; do not open public port 22.
Access matrix (fill with PRIVATE evidence, not passwords):
```
Source         Pi reachable   SSH authorized as non-root   Remote read-only proof
Laptop 1 Alex  [ ]            [ ]                          [ ]
Laptop 2 Ramona[ ]            [ ]                          [ ]
Unauthor. acct [ ] denied     [ ] denied                   N/A
```
Authorization is not a command; owner must arrange it in the Tailscale admin policy and local Linux accounts. Ramona should not automatically gain shell root or access to live customer data.

## 4. Deploy **reviewed** repo to Pi, not copy files by USB
As your approved non-root Pi user in isolated workspace:
```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/as4584/Raspberry-4-model-B-8gb-ram-.git pi-receptionist
cd pi-receptionist
git rev-parse HEAD
bash scripts/pi_preflight.sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
```
For an existing clone, `git fetch origin`; inspect `git status` and `git log` before `git pull --ff-only`. Do not overwrite local changes. For Ramona use a **different** checkout and user from Alex; no shared working-tree writes.
Python package installs in the venv, not global system. A clean Pi test run establishes only starter correctness, not model performance. If Pi wheel dependency conflicts occur, document and pin versions rather than disabling tests.

## 5. Private health check; no router changes needed
On Pi, after setup:
```bash
# TEST ONLY: localhost, unsigned allowed in an isolated test instance
DEV_ALLOW_UNSIGNED=1 python -m uvicorn receptionist.server:app --host 127.0.0.1 --port 8000
```
In a SECOND terminal on the SAME Pi, `curl -fsS http://127.0.0.1:8000/healthz` should report status ok.
From Alex's or Ramona's laptop, open a terminal that stays connected:
```bash
ssh -L 8000:127.0.0.1:8000 <PI_USER>@lex-receptionist-pi
```
On the originating laptop, `curl http://127.0.0.1:8000/healthz` (PowerShell can use `curl.exe` or `Invoke-RestMethod`). This tunnel forwards localhost *privately*; it does not expose the dev endpoint to the public or to all tailnet peers. If local laptop port 8000 is occupied, choose 18000 on the left side. Avoid `--host 0.0.0.0` and public tunnel for unsigned mode.
Optional, **only after auth review:** Tailscale Serve can expose HTTPS to the tailnet for a signed/service-safe endpoint, not to Twilio PSTN. See https://tailscale.com/docs/features/tailscale-serve . **Tailscale Serve is private, not the Twilio public WSS endpoint.**

## 6. Scripts / benchmark coordination
`bash scripts/pi_preflight.sh`: read-only checks Pi identity, arch/RAM, Python and Tailscale presence. It must exit nonzero if wrong hardware or incomplete environment.
`bash scripts/pi_smoke.sh`: intentionally minimal isolated local tests; check implementation and manual authorization before running. No automatic reboots/firmware/package-manager or public network mutations.
Do not benchmark on Ramona's laptop and report it as Pi data. Coordinate exclusive Pi measurement windows. Ramona may inspect/read/run tests with a non-root account, but cannot restart/upgrade a customer-facing service. Separate test SQLite file, model hashes, data and logs.
Never commit: `.env`, Tailscale auth keys, tokens, raw transcripts/recordings, caller IDs or any tailnet private details. Use reviewed `.env.example`.

## 7. S1 TDD locks and evidence
**RED tests/reproductions:** Pi identity check fails on non-ARM64/wrong Pi; Tailscale missing/offline; unauthorized user denied by ACL; health check not reachable from a laptop; repo not installable in venv. Create test harness around shell preflight with injected fixture files/commands (without mocking real pass evidence).
**GREEN artifacts:**
1. Pi `uname -m`, model and RAM read *on actual device*; `git rev-parse HEAD`.
2. On BOTH laptops, `tailscale ping` and SSH by own authorized identity to Pi, a remote command returning Pi model; record pass/fail per laptop.
3. Pi venv `python -m pytest -q` with counts; no disabled tests.
4. `/healthz` reached over each laptop's SSH tunnel, Dev server bound to Pi 127.0.0.1 only.
5. Verify one can still recover local console/SSH after a controlled Pi reboot (owner approval). Ensure unsigned dev is stopped when testing ends.
6. ACL negative test or reviewed least-privilege policy with explicit acknowledgement. No default world access.
**Gate G1 is BLOCKED until items 1–5 are actually performed, not merely specified.**

## 8. Recovery / common blockers
- SSH times out: Pi online? both Tailscale status? ACL port22? Tailscale SSH enabled? normal sshd running? MagicDNS vs IP?
- Device appears but no SSH: Tailscale alone is connectivity, not SSH authentication. Fix policy, OS user or ssh daemon, never router port forwarding.
- Python pip fails: check python >=3.11, apt python3-venv, disk free and pinned wheels; don't `sudo pip`.
- Port forwarding gives wrong service: ensure Pi app listens loopback and tunnel uses intended local port.
- Pi overheats/restarts: log power/thermal; stop; fix cooling/PSU; never overclock to pass a synthetic target.
- Live Twilio fails: do NOT use this private dev URL for public Twilio webhooks. G7 has a separate signed HTTPS/WSS runbook and security review.
