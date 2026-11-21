#!/usr/bin/env bash
# Inbound firewall for the receptionist Pi (owner-approved 2026-11-21; from the kit).
#   SSH only over Tailscale; SIP-TLS 5061/tcp + SRTP 16384-16484/udp for Telnyx; rest denied.
#   ARI (8088), the app (8000) and SIPp (5070) are loopback-only and never opened.
# Safety: a rollback timer runs `ufw disable` in 5 minutes. Prove SSH still works from a
# NEW Tailscale session, then disarm it:   bash scripts/pi_firewall.sh --confirm
set -euo pipefail
dry="${DRY_RUN:-0}"
uid="${BOOTSTRAP_UID:-$(id -u)}"
[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user (sudo is used per step)" >&2; exit 61; }
step() { echo "PLAN: $1"; [[ "$dry" == "1" ]] || bash -c "$1"; }

if [[ "${1:-}" == "--confirm" ]]; then
  step "sudo systemctl stop pi-firewall-rollback.timer pi-firewall-rollback.service 2>/dev/null || true"
  echo "FIREWALL_CONFIRMED: rollback disarmed"
  exit 0
fi

step "command -v ufw >/dev/null || sudo apt-get install -y -q ufw"
step "sudo systemd-run --unit=pi-firewall-rollback --on-active=300 /usr/sbin/ufw disable"
step "sudo ufw --force reset"
step "sudo ufw default deny incoming"
step "sudo ufw default allow outgoing"
step "sudo ufw allow in on tailscale0 to any port 22 proto tcp comment 'SSH via Tailscale only'"
step "sudo ufw allow 5061/tcp comment 'SIP over TLS (Telnyx)'"
step "sudo ufw allow 16384:16484/udp comment 'SRTP media'"
step "sudo ufw --force enable"
step "sudo ufw status verbose"
echo "FIREWALL_ON: auto-rollback in 5 min unless you run: bash scripts/pi_firewall.sh --confirm"
