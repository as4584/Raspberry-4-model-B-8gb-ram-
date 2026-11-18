#!/usr/bin/env bash
# Run a command with Doppler-injected secrets (Twilio etc.), e.g.
#   bash scripts/with_secrets.sh .venv/bin/python -m uvicorn receptionist.server:app --host 127.0.0.1
set -euo pipefail
f="${PI_SECRETS_DIR:-$HOME/.config/pi-receptionist}/doppler.env"
[[ -r "$f" ]] || { echo "FAIL: no $f; run scripts/pi_doppler_token.sh first" >&2; exit 44; }
token="$(sed -n 's/^DOPPLER_TOKEN=//p' "$f")"
[[ -n "$token" ]] || { echo "FAIL: $f has no DOPPLER_TOKEN" >&2; exit 45; }
DOPPLER_TOKEN="$token" exec doppler run -- "$@"
