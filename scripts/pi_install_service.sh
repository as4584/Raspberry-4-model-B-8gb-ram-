#!/usr/bin/env bash
# Install/upgrade the receptionist as a hardened systemd service, pinned to a
# reviewed commit. Run on the Pi as your normal user (sudo is used per step):
#   bash scripts/pi_install_service.sh <git-sha>    # install or upgrade
#   bash scripts/pi_install_service.sh --uninstall  # stop + remove unit; KEEPS bookings
# Code lives root-owned in /opt/pi-receptionist/app (the service can't modify
# it); bookings live in /var/lib/pi-receptionist/data. Binds 127.0.0.1 only.
set -euo pipefail
dry="${DRY_RUN:-0}"
uid="${BOOTSTRAP_UID:-$(id -u)}"
repo="${REPO_URL:-https://github.com/as4584/Raspberry-4-model-B-8gb-ram-.git}"
app=/opt/pi-receptionist/app
data=/var/lib/pi-receptionist/data
unit=/etc/systemd/system/pi-receptionist.service
here="$(cd "$(dirname "$0")/.." && pwd)"

[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user (sudo is used per step)" >&2; exit 61; }
step() { echo "PLAN: $1"; [[ "$dry" == "1" ]] || bash -c "$1"; }

if [[ "${1:-}" == "--uninstall" ]]; then
  step "sudo systemctl disable --now pi-receptionist.service pi-receptionist-backup.timer || true"
  step "sudo rm -f $unit && sudo systemctl daemon-reload"
  echo "UNINSTALLED (bookings kept)"
  exit 0
fi

sha="${1:-}"
[[ "$sha" =~ ^[0-9a-f]{7,40}$ ]] || { echo "FAIL: pass the reviewed git SHA to deploy" >&2; exit 60; }

step "id receptionist >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin receptionist"
step "[ -d $app/.git ] || sudo git clone -q $repo $app"
step "sudo git -C $app fetch -q origin && sudo git -C $app checkout --detach $sha"
step "sudo python3 -m venv $app/.venv && sudo $app/.venv/bin/python -m pip install -q \"$app[brain]\""
step "sudo chown -R root:root /opt/pi-receptionist/app"
step "sudo install -d -o receptionist -g receptionist -m 750 $data"
step "sudo install -d -o receptionist -g receptionist -m 750 /var/backups/pi-receptionist"
step "sudo install -m 644 $here/deploy/pi-receptionist.service $unit && sudo systemctl daemon-reload"
step "sudo install -m 644 $here/deploy/pi-receptionist-backup.service $here/deploy/pi-receptionist-backup.timer /etc/systemd/system/ && sudo systemctl daemon-reload"
step "sudo systemctl enable --now pi-receptionist-backup.timer"
step "sudo systemctl enable --now pi-receptionist.service && sudo systemctl restart pi-receptionist.service"
step "for i in \$(seq 30); do curl -fsS http://127.0.0.1:8000/healthz && exit 0; sleep 1; done; exit 1"
echo
echo "SERVICE_OK: pi-receptionist @ $sha on 127.0.0.1:8000"
