#!/usr/bin/env bash
# Install our minimal Asterisk config + systemd service (after pi_build_asterisk.sh).
# Secrets come from the environment (run under scripts/with_secrets.sh for Doppler):
#   TELNYX_SIP_USERNAME / TELNYX_SIP_PASSWORD  - optional; without them only SIPp works
#   ARI_PASSWORD                                - generated once if absent
set -euo pipefail
dry="${DRY_RUN:-0}"
uid="${BOOTSTRAP_UID:-$(id -u)}"
here="$(cd "$(dirname "$0")/.." && pwd)"
cfg="$here/deploy/asterisk"
etc=/etc/asterisk
arienv=/etc/pi-receptionist/ari.env

[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user (sudo is used per step)" >&2; exit 61; }
step() { echo "PLAN: $1"; [[ "$dry" == "1" ]] || bash -c "$1"; }

step "sudo install -d -m 750 -o root -g asterisk $etc /etc/pi-receptionist"
step "sudo test -s $arienv || (umask 077; printf 'ARI_PASSWORD=%s\n' \"\$(head -c 24 /dev/urandom | base64 | tr -d '/+=')\" | sudo tee $arienv >/dev/null; sudo chmod 600 $arienv)"
step "printf '[directories]\nastetcdir => $etc\n[options]\nrunuser = asterisk\nrungroup = asterisk\n' | sudo tee $etc/asterisk.conf >/dev/null"
for f in http.conf websocket_client.conf chan_websocket.conf extensions.conf modules.conf; do
  step "sudo install -m 640 -o root -g asterisk $cfg/$f $etc/$f"
done
step "sudo bash -c 'set -a; . $arienv; set +a; envsubst < $cfg/ari.conf.tmpl > $etc/ari.conf' && sudo chown root:asterisk $etc/ari.conf && sudo chmod 640 $etc/ari.conf"
# Values reach envsubst through the exported environment, never through the command
# text, which step() prints.
export TELNYX_SIP_USERNAME="${TELNYX_SIP_USERNAME:-}" TELNYX_SIP_PASSWORD="${TELNYX_SIP_PASSWORD:-}"
step "envsubst '\$TELNYX_SIP_USERNAME \$TELNYX_SIP_PASSWORD' < $cfg/pjsip.conf.tmpl | sudo tee $etc/pjsip.conf >/dev/null && sudo chown root:asterisk $etc/pjsip.conf && sudo chmod 640 $etc/pjsip.conf"
if [[ -z "${TELNYX_SIP_USERNAME:-}" || -z "${TELNYX_SIP_PASSWORD:-}" ]]; then
  # No provider yet: drop every telnyx section so Asterisk doesn't try to register.
  step "sudo sed -i '/^\[telnyx/,/^\$/d' $etc/pjsip.conf"
  echo "SIPP_ONLY: Telnyx credentials not set; only local SIPp calls will be accepted"
fi
step "sudo install -m 644 $here/deploy/asterisk.service /etc/systemd/system/asterisk.service && sudo systemctl daemon-reload"
step "sudo systemctl enable --now asterisk.service && sudo systemctl restart asterisk.service"
step "for i in \$(seq 20); do sudo asterisk -rx 'module show like websocket' 2>/dev/null | grep -q chan_websocket && exit 0; sleep 1; done; exit 1"
echo "ASTERISK_OK"
