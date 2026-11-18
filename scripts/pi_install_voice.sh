#!/usr/bin/env bash
# Switch the hardened receptionist service to real local speech (or back to mock).
# The unit has ProtectHome=yes, so binaries and models are copied root-owned into
# /opt/pi-receptionist/speech and selected via /etc/pi-receptionist/voice.env.
#   bash scripts/pi_install_voice.sh <whisper-bin-dir> <whisper-model> <piper-dir> <piper-model>
#   bash scripts/pi_install_voice.sh --off
set -euo pipefail
dry="${DRY_RUN:-0}"
uid="${BOOTSTRAP_UID:-$(id -u)}"
speech=/opt/pi-receptionist/speech
envfile=/etc/pi-receptionist/voice.env

[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user (sudo is used per step)" >&2; exit 61; }
step() { echo "PLAN: $1"; [[ "$dry" == "1" ]] || bash -c "$1"; }
restart() {
  step "sudo systemctl restart pi-receptionist.service"
  step "for i in \$(seq 30); do curl -fsS http://127.0.0.1:8000/healthz | grep -q '\"voice_backend\":\"$1\"' && exit 0; sleep 1; done; exit 1"
}

if [[ "${1:-}" == "--off" ]]; then
  step "sudo install -d -m 755 /etc/pi-receptionist && printf 'VOICE_BACKEND=mock\n' | sudo tee $envfile >/dev/null"
  restart mock
  echo "VOICE_OFF: mock backend"
  exit 0
fi

[[ $# -eq 4 ]] || { echo "FAIL: pass <whisper-bin-dir> <whisper-model> <piper-dir> <piper-model>" >&2; exit 60; }
wbin="$1"; wmodel="$2"; pdir="$3"; pmodel="$4"
wm="$speech/models/$(basename "$wmodel")"; pm="$speech/models/$(basename "$pmodel")"

step "sudo install -d -m 755 $speech/whisper $speech/piper $speech/models /etc/pi-receptionist"
step "sudo cp -a $wbin/. $speech/whisper/ && sudo cp -a $pdir/. $speech/piper/"
step "sudo cp $wmodel $pmodel ${pmodel}.json $speech/models/ 2>/dev/null || sudo cp $wmodel $pmodel $speech/models/"
step "sudo chown -R root:root $speech && sudo chmod -R a+rX,go-w $speech"
step "printf 'VOICE_BACKEND=local\nWHISPER_CLI=$speech/whisper/whisper-cli\nWHISPER_MODEL=$wm\nPIPER_CLI=$speech/piper/piper\nPIPER_MODEL=$pm\n' | sudo tee $envfile >/dev/null"
restart local
echo "VOICE_OK: local speech ($(basename "$wmodel"), $(basename "$pmodel"))"
