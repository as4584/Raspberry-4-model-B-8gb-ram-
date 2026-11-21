#!/usr/bin/env bash
# Install our minimal Asterisk config + systemd service (after pi_build_asterisk.sh).
# Secrets are not handled here: see deploy/pi-receptionist-secrets.service (Doppler -> RAM).
set -euo pipefail
dry="${DRY_RUN:-0}"
uid="${BOOTSTRAP_UID:-$(id -u)}"
here="$(cd "$(dirname "$0")/.." && pwd)"
cfg="$here/deploy/asterisk"
etc=/etc/asterisk

[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user (sudo is used per step)" >&2; exit 61; }
step() { echo "PLAN: $1"; [[ "$dry" == "1" ]] || bash -c "$1"; }

step "sudo install -d -m 750 -o root -g asterisk $etc /etc/pi-receptionist"
step "printf '[directories]
astetcdir => $etc
[options]
runuser = asterisk
rungroup = asterisk
' | sudo tee $etc/asterisk.conf >/dev/null"
for f in http.conf ari.conf websocket_client.conf chan_websocket.conf extensions.conf modules.conf pjsip.conf rtp.conf logger.conf; do
  step "sudo install -m 640 -o root -g asterisk $cfg/$f $etc/$f"
done
step "sudo install -d -o asterisk -g asterisk -m 750 /var/spool/asterisk/monitor"
# Review mode (owner-approved): only numbers in REVIEW_CALLERS (/etc/pi-receptionist/telephony.env)
# are recorded; files are root/asterisk-only and deleted after 14 days.
step "sudo install -d -o asterisk -g asterisk -m 700 /var/spool/asterisk/review"
step "sudo bash -c '. /etc/pi-receptionist/telephony.env 2>/dev/null; { echo \"[globals]\"; for n in \$(echo \"\${REVIEW_CALLERS:-}\" | tr , \" \"); do d=\$(echo \"\$n\" | tr -cd 0-9); [ \${#d} -ge 10 ] && echo \"REVIEW_\${d: -10}=1\"; done; } > /etc/asterisk/review.conf' && sudo chown root:asterisk /etc/asterisk/review.conf && sudo chmod 640 /etc/asterisk/review.conf"
step "sudo install -m 644 $here/deploy/pi-receptionist-review-cleanup.service $here/deploy/pi-receptionist-review-cleanup.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now pi-receptionist-review-cleanup.timer"
# Secrets never pass through this script: pi-receptionist-secrets.service renders them from
# Doppler (read-only prd token in /etc/pi-receptionist/doppler.env) into RAM at every boot.
step "sudo install -d -m 750 -o root -g asterisk /opt/pi-receptionist/templates"
step "sudo install -m 640 -o root -g asterisk $cfg/pjsip-secrets.conf.tmpl $cfg/ari-users.conf.tmpl /opt/pi-receptionist/templates/"
step "sudo rm -f /etc/asterisk/ari.conf.rendered /etc/pi-receptionist/ari.env"
step "sudo install -m 644 $here/deploy/pi-receptionist-secrets.service /etc/systemd/system/ && sudo systemctl daemon-reload"
step "sudo systemctl enable --now pi-receptionist-secrets.service && sudo systemctl restart pi-receptionist-secrets.service"
step "sudo install -m 644 $here/deploy/asterisk.service /etc/systemd/system/asterisk.service && sudo systemctl daemon-reload"
step "sudo systemctl enable --now asterisk.service && sudo systemctl restart asterisk.service"
step "for i in \$(seq 20); do sudo asterisk -rx 'module show like websocket' 2>/dev/null | grep -q chan_websocket && exit 0; sleep 1; done; exit 1"
echo "ASTERISK_OK"
