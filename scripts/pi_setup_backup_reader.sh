#!/usr/bin/env bash
# One-time, on the Pi as your normal user: create 'backupreader', whose SSH key
# can only stream the newest bookings backup (forced command, no shell, no
# forwarding, no sudo). Used by the desktop's off-device backup pull.
#   bash scripts/pi_setup_backup_reader.sh "ssh-ed25519 AAAA... desktop-backup"
set -euo pipefail
dry="${DRY_RUN:-0}"
uid="${BOOTSTRAP_UID:-$(id -u)}"
pub="${1:-}"
here="$(cd "$(dirname "$0")" && pwd)"
[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user (sudo is used per step)" >&2; exit 73; }
[[ "$pub" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]{40,}(\ [A-Za-z0-9._@-]+)?$ ]] \
  || { echo "FAIL: pass one ssh-ed25519 PUBLIC key (no quotes, no options)" >&2; exit 72; }
step() { echo "PLAN: $1"; [[ "$dry" == "1" ]] || bash -c "$1"; }

step "id backupreader >/dev/null 2>&1 || sudo useradd --system --create-home --shell /bin/sh --groups receptionist backupreader"
step "sudo install -o root -g root -m 755 '$here/pi_backup_export.sh' /usr/local/bin/pi-backup-export"
step "sudo install -d -o backupreader -g backupreader -m 700 /home/backupreader/.ssh"
step "echo 'restrict,command=\"/usr/local/bin/pi-backup-export\" $pub' | sudo tee /home/backupreader/.ssh/authorized_keys >/dev/null"
step "sudo chown backupreader:backupreader /home/backupreader/.ssh/authorized_keys && sudo chmod 600 /home/backupreader/.ssh/authorized_keys"
echo "BACKUP_READER_OK"
