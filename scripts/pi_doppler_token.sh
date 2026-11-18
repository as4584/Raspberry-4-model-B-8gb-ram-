#!/usr/bin/env bash
# One-time, on the Pi as your normal user: store a Doppler SERVICE token
# (read-only, scoped to pi-receptionist/prd) so apps can get Twilio secrets.
#   bash scripts/pi_doppler_token.sh      # then paste the token; it is not shown
# The token goes to ~/.config/pi-receptionist/doppler.env (0600), never into
# the repo, shell history, or a command line.
set -euo pipefail
uid="${BOOTSTRAP_UID:-$(id -u)}"
dir="${PI_SECRETS_DIR:-$HOME/.config/pi-receptionist}"
[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user, not root" >&2; exit 40; }
command -v doppler >/dev/null || { echo "FAIL: doppler CLI not installed" >&2; exit 41; }

[[ -t 0 ]] && printf 'Paste Doppler service token (dp.st...): ' >&2
IFS= read -rs token || true
token="${token%$'\r'}"  # pastes from Windows terminals can carry a CR
[[ -t 0 ]] && echo >&2
[[ "$token" =~ ^dp\.st\.[A-Za-z0-9_.-]{20,}$ ]] \
  || { echo "FAIL: not a Doppler service token (expected dp.st.<config>.<...>)" >&2; exit 42; }

umask 077
mkdir -p "$dir"
tmp="$dir/doppler.env.tmp"
printf 'DOPPLER_TOKEN=%s\n' "$token" > "$tmp"
chmod 600 "$tmp"

echo "Verifying (secret names only, values are never shown):"
if ! DOPPLER_TOKEN="$token" doppler secrets --only-names; then
  rm -f "$tmp"
  echo "FAIL: Doppler rejected the token; nothing saved" >&2
  exit 43
fi
mv -f "$tmp" "$dir/doppler.env"
echo "SAVED $dir/doppler.env (0600)"
