#!/usr/bin/env bash
# Forced command for the restricted 'backupreader' SSH key (see
# scripts/pi_setup_backup_reader.sh). Streams the newest bookings snapshot and
# its sha256 sidecar as a tar on stdout. It ignores whatever command the client
# asked for (SSH_ORIGINAL_COMMAND): this key can do nothing else.
set -euo pipefail
dir="${BACKUP_DIR:-/var/backups/pi-receptionist}"
latest=""
for f in "$dir"/bookings-*.sqlite; do
  [[ -e "$f" ]] && latest="$f"     # glob sorts by timestamp; keep the last
done
[[ -n "$latest" ]] || { echo "FAIL: no snapshot in $dir" >&2; exit 70; }
side="${latest%.sqlite}.sha256"
[[ -r "$side" ]] || { echo "FAIL: snapshot has no sha256 sidecar" >&2; exit 71; }
exec tar -C "$dir" -cf - "$(basename "$latest")" "$(basename "$side")"
