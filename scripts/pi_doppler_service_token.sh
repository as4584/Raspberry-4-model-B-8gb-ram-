#!/usr/bin/env bash
# Mint a read-only Doppler service token scoped to lexmakesit-receptionist/prd and
# install it on the Pi as /etc/pi-receptionist/doppler.env (root:root 600).
# Adapted from the owner's kit (03-pi-install-token.sh): the token is held only in this
# process's memory and sent over ssh stdin; it is never printed, never in argv, and a
# failed or empty mint can never overwrite the Pi's existing token.
#   bash scripts/pi_doppler_service_token.sh prototype@<pi-tailscale-ip>
set -euo pipefail
PROJECT=lexmakesit-receptionist
PI="${1:?usage: $0 <user@pi>}"
NAME="pi-prd-$(date +%Y%m%d-%H%M%S)"

token="$(doppler configs tokens create "$NAME" --project "$PROJECT" --config prd --access read --plain)" || {
  echo "FAIL: could not mint token" >&2; exit 1; }
[[ "$token" =~ ^dp\.st\. ]] || { unset token; echo "FAIL: minted value is not a service token" >&2; exit 1; }

# Remote side: write a root-only temp file, then rename over the old one (atomic).
printf '%s' "$token" | ssh "$PI" 'set -e
  sudo install -d -m 0700 -o root -g root /etc/pi-receptionist
  t=$(sudo mktemp /etc/pi-receptionist/.doppler.env.XXXXXX)
  { printf "DOPPLER_TOKEN="; cat; printf "\n"; } | sudo tee "$t" >/dev/null
  sudo chmod 600 "$t" && sudo chown root:root "$t" && sudo mv -f "$t" /etc/pi-receptionist/doppler.env
  echo "token installed on $(hostname)"'
unset token
echo "Token '$NAME' (read-only, prd only) installed. Revoke anytime:"
echo "  doppler configs tokens revoke $NAME --project $PROJECT --config prd"
