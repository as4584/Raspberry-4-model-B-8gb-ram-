#!/usr/bin/env bash
# Faster boot AFTER first-boot provisioning succeeded. Reversible:
#   bash scripts/pi_tune_boot.sh            # apply
#   bash scripts/pi_tune_boot.sh --revert   # undo
# Measured bottlenecks (PI4, 2026-11-17, 71 s boot): NetworkManager-wait-online
# 29 s (pulled in by cloud-init), man-db 29 s, cloud-init ~10 s every boot.
# Never edits config.txt/cmdline.txt/EEPROM, never reboots, never touches ssh,
# tailscaled or NetworkManager itself. DRY_RUN=1 prints the plan only.
set -euo pipefail
dry="${DRY_RUN:-0}"
marker="${TUNE_MARKER:-/var/lib/pi-receptionist/bootstrapped}"
uid="${BOOTSTRAP_UID:-$(id -u)}"
dropin_dir=/etc/systemd/system/NetworkManager-wait-online.service.d
dropin="$dropin_dir/10-timeout.conf"

[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user (sudo is used per step)" >&2; exit 51; }
step() { echo "PLAN: $1"; [[ "$dry" == "1" ]] || bash -c "$1"; }

if [[ "${1:-}" == "--revert" ]]; then
  step "sudo rm -f /etc/cloud/cloud-init.disabled"
  step "sudo rm -f $dropin"
  step "sudo systemctl unmask man-db.timer man-db.service"
  step "sudo touch /var/lib/man-db/auto-update"
  step "sudo systemctl enable bluetooth.service"
  step "sudo systemctl daemon-reload"
  echo "REVERTED: takes effect next boot"
  exit 0
fi

[[ -e "$marker" ]] || { echo "FAIL: first-boot provisioning not finished ($marker missing); cloud-init still needed" >&2; exit 50; }

# cloud-init only needs to run on the first boot; network config persists in /etc/netplan.
step "sudo touch /etc/cloud/cloud-init.disabled"
# Backstop: never wait more than 10 s for 'network online'.
step "sudo mkdir -p $dropin_dir && printf '[Service]\nExecStart=\nExecStart=/usr/bin/nm-online -s -q --timeout=10\n' | sudo tee $dropin >/dev/null"
# Manual-page index rebuilds: not needed on a headless appliance.
step "sudo systemctl mask man-db.timer man-db.service"
step "sudo rm -f /var/lib/man-db/auto-update"
# Bluetooth is unused by the receptionist.
step "sudo systemctl disable --now bluetooth.service"
step "sudo systemctl daemon-reload"
echo "TUNED: measure with 'systemd-analyze' after the next boot"
