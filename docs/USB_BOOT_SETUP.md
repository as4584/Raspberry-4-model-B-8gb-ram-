# USB boot → ready Pi in one command (S1)
Owner: Alex (physical). Gate: G1 stays **BLOCKED** until the evidence in step 6 is attached to issue #2.
Detailed policy and SSH/ACL choices: `docs/PI_TAILSCALE_RUNBOOK.md`.

## 0. Unattended path (agent on Laptop 1; human just plugs in)
1. Agent flashes the verified Trixie Lite arm64 image with `rpi-imager --cli` (one UAC click). The script aborts unless the target is the expected USB disk.
2. `python scripts/make_cloudinit.py <bootfs> --wifi-file ~/.secrets/pi-wifi.txt --login-file ~/.secrets/pi-login.txt --ssh-pubkey ~/.ssh/id_ed25519.pub` writes the user, hostname, key-only SSH and Wi-Fi. Secrets stay in `~/.secrets`, never in git.
3. `python scripts/stage_bootfs.py <bootfs> --autorun [--tailscale-authkey-file ~/.secrets/ts-pi.key]`
4. Plug into the Pi and power on. cloud-init creates the user, then `firstboot_install.sh` installs `pi-receptionist-firstboot.service`, which runs the bootstrap (retrying until it succeeds). Progress: `journalctl -u pi-receptionist-firstboot`, `~/pi-evidence/`.
5. With no auth key, the log shows `BLOCKER:`. Owner runs `sudo tailscale up --hostname=lex-receptionist-pi` once and approves the link, then `sudo systemctl restart pi-receptionist-firstboot`.

## 1. Flash the USB (laptop, manual path)
Raspberry Pi Imager → **Device:** Raspberry Pi 4 → **OS:** Raspberry Pi OS **(64-bit)** Lite (or Desktop) → **Storage:** the USB drive.
In **OS customisation** (Edit settings):
- Hostname `lex-receptionist-pi`
- Username: your own choice (not `pi`) plus a strong password
- Wi-Fi only if you have no Ethernet
- Services → **Enable SSH → Allow public-key authentication only**, then paste your laptop's `~/.ssh/id_ed25519.pub`

Write, then let Imager verify.

## 2. Stage the kit (laptop)
Unplug and replug the USB. The `bootfs` partition mounts (on Windows it gets a drive letter, e.g. `E:`).
```powershell
cd C:\Users\AlexS\Projects\rpi4
.venv\Scripts\python scripts\stage_bootfs.py E:\
```
This only adds `pi-receptionist\` (bootstrap, preflight, README, SHA-256 manifest). Eject safely.

## 3. Boot (Pi)
Remove any SD card, plug the USB into a **blue USB 3** port, connect Ethernet, fan and the official 5V/3A PSU.
Pi 4 boards with a 2020+ bootloader try SD and then USB. If it doesn't boot, flash a microSD with Imager → Misc utility images → Bootloader → **USB Boot**, boot it once (green light blinks steadily), then remove it. That updates the EEPROM; it's an owner action and the agent never does it.

## 4. Run the bootstrap (Pi, as your user, not root)
From the keyboard, or `ssh <you>@lex-receptionist-pi.local` on the LAN:
```bash
cd /boot/firmware/pi-receptionist && sha256sum -c MANIFEST.sha256
DRY_RUN=1 bash pi_bootstrap.sh     # review the plan
bash pi_bootstrap.sh               # do it
```
It installs base packages and Tailscale (official signed apt repo), then runs `sudo tailscale up --hostname=lex-receptionist-pi`. **Open the printed URL and approve it in your browser.** After that it clones the repo to `~/projects/pi-receptionist`, creates the venv, runs `pytest` and runs `scripts/pi_preflight.sh`. The log goes to `~/pi-evidence/`. It's safe to re-run.

## 5. Tailnet access (owner, Tailscale admin console)
Invite Ramona's **own** account. Set an ACL that lets only Alex + Ramona reach the Pi on tcp:22 as non-root users. Install Tailscale on each laptop (Laptop 1 currently has none: issue #3). Don't enable `tailscale set --ssh` until the ACL is reviewed.

## 6. Evidence for issue #2 (sanitize tailnet IPs/names first)
- `~/pi-evidence/bootstrap-*.log` showing `evidence_kind=PI4_CANDIDATE`, `PI4_PREFLIGHT_OK`, pytest counts and git SHA
- From each laptop: `tailscale ping lex-receptionist-pi` and `ssh <user>@lex-receptionist-pi cat /proc/device-tree/model`
- `bash scripts/pi_smoke.sh`, then the `/healthz` check through `ssh -L 8000:127.0.0.1:8000` (runbook §5)
