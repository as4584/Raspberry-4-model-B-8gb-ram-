#!/usr/bin/env bash
# Run by pi-receptionist-secrets.service under `doppler run` (read-only prd token).
# Renders credentials into RAM (/run is tmpfs: gone at power-off, never on the USB disk).
# Prints file and variable NAMES only, never values. A missing secret skips just its file.
set -euo pipefail
umask 077
AST="${ASTERISK_SECRETS_DIR:-/run/asterisk-secrets}"
APP="${APP_SECRETS_DIR:-/run/pi-receptionist}"
TPL="${TEMPLATES_DIR:-/opt/pi-receptionist/templates}"
GRP="${SECRETS_OWNER_GROUP-asterisk}"           # empty in tests (no asterisk group there)
mkdir -p "$AST" "$APP"; chmod 750 "$AST"; chmod 700 "$APP"
[[ -n "$GRP" ]] && chgrp "$GRP" "$AST"

have() { local v; for v in "$@"; do [[ -n "${!v:-}" ]] || return 1; done; }
missing() { local v out=(); for v in "$@"; do [[ -n "${!v:-}" ]] || out+=("$v"); done; echo "${out[*]}"; }

put() { # $1=dest $2=mode ; content on stdin; atomic replace
  local tmp; tmp="$(mktemp "$1.XXXXXX")"; cat > "$tmp"; chmod "$2" "$tmp"
  [[ -n "$GRP" && "$2" == 640 ]] && chgrp "$GRP" "$tmp"
  mv -f "$tmp" "$1"
}

if have TELNYX_SIP_USERNAME TELNYX_SIP_PASSWORD; then
  envsubst '$TELNYX_SIP_USERNAME $TELNYX_SIP_PASSWORD' < "$TPL/pjsip-secrets.conf.tmpl" | put "$AST/pjsip-secrets.conf" 640
  echo "rendered pjsip-secrets.conf"
else
  rm -f "$AST/pjsip-secrets.conf"; echo "skipped pjsip-secrets.conf (missing: $(missing TELNYX_SIP_USERNAME TELNYX_SIP_PASSWORD))"
fi
if have ARI_USERNAME ARI_PASSWORD; then
  envsubst '$ARI_USERNAME $ARI_PASSWORD' < "$TPL/ari-users.conf.tmpl" | put "$AST/ari-users.conf" 640
  printf 'ARI_USERNAME=%s\nARI_PASSWORD=%s\n' "$ARI_USERNAME" "$ARI_PASSWORD" | put "$APP/app.env" 600
  echo "rendered ari-users.conf app.env"
else
  rm -f "$AST/ari-users.conf" "$APP/app.env"; echo "skipped ari-users.conf app.env (missing: $(missing ARI_USERNAME ARI_PASSWORD))"
fi
