#!/usr/bin/env bash
# Runs as ROOT once, from Imager's firstrun.sh or cloud-init runcmd (hooked by
# stage_bootfs.py --autorun). Installs a oneshot service that runs
# pi_bootstrap.sh as the normal user once the network is up, retrying until it
# succeeds. Moves any staged Tailscale key off the FAT boot partition.
# DRY_RUN=1 prints the plan; FIRSTBOOT_* are test-only overrides.
set -euo pipefail
dry="${DRY_RUN:-0}"
kit="${FIRSTBOOT_KIT:-$(cd "$(dirname "$0")" && pwd)}"
user="${FIRSTBOOT_USER-$(getent passwd 1000 | cut -d: -f1)}"
[[ -n "$user" ]] || { echo "FAIL: no normal user (uid 1000); set one in Imager OS customisation" >&2; exit 30; }
unit=/etc/systemd/system/pi-receptionist-firstboot.service

step() { echo "PLAN: $1"; [[ "$dry" == "1" ]] || bash -c "$1"; }

step "install -d -m 755 /opt/pi-receptionist-kit /etc/pi-receptionist"
step "install -m 755 '$kit/pi_bootstrap.sh' '$kit/pi_preflight.sh' /opt/pi-receptionist-kit/"
if [[ -e "$kit/tailscale.authkey" ]]; then
  step "install -m 600 -o root -g root '$kit/tailscale.authkey' /etc/pi-receptionist/tailscale.authkey"
  step "shred -u $kit/tailscale.authkey || rm -f $kit/tailscale.authkey"
fi

read -r -d '' body <<EOF || true
[Unit]
Description=Pi receptionist unattended first-boot bootstrap
Wants=network-online.target
After=network-online.target
ConditionPathExists=!/var/lib/pi-receptionist/bootstrapped
StartLimitIntervalSec=0

[Service]
Type=oneshot
User=$user
Environment=NONINTERACTIVE=1
ExecStart=/bin/bash /opt/pi-receptionist-kit/pi_bootstrap.sh
Restart=on-failure
RestartSec=120
TimeoutStartSec=3600

[Install]
WantedBy=multi-user.target
EOF
echo "$body" | sed 's/^/UNIT: /'
[[ "$dry" == "1" ]] || printf '%s\n' "$body" > "$unit"
step "systemctl enable pi-receptionist-firstboot.service"
step "systemctl start --no-block pi-receptionist-firstboot.service || true"
echo "FIRSTBOOT_INSTALLED user=$user"
