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
here="$(cd "$(dirname "$0")/.." && pwd)"
wsrc="${wbin%/build/bin}"                       # whisper.cpp tree that produced <whisper-bin-dir>

step "sudo install -d -m 755 $speech/whisper $speech/piper $speech/models /etc/pi-receptionist"
# Resident whisper (#28: ~0.7 s per turn vs ~7 s for whisper-cli), built next to whisper-cli.
step "bash $here/native/build_whisper_daemon.sh $wsrc $wbin"
step "sudo cp -a $wbin/. $speech/whisper/ && sudo cp -a $pdir/. $speech/piper/"
step "sudo cp $wmodel $pmodel ${pmodel}.json $speech/models/ 2>/dev/null || sudo cp $wmodel $pmodel $speech/models/"
step "sudo chown -R root:root $speech && sudo chmod -R a+rX,go-w $speech"
# Prove both binaries start under the service's own sandbox before switching: whisper-cli's
# build-time library path points under ~/, which ProtectHome hides (found on the Pi).
sandbox="sudo systemd-run --wait --pipe --quiet -p User=receptionist -p ProtectHome=yes -p ProtectSystem=strict -p PrivateTmp=yes -p PrivateDevices=yes -p NoNewPrivileges=yes -E LD_LIBRARY_PATH=$speech/whisper"
step "$sandbox $speech/whisper/whisper-cli --help >/dev/null 2>&1"
step "$sandbox $speech/whisper/whisper-daemon $wm 1 </dev/null | grep -qx READY"
step "echo test | $sandbox sh -c '$speech/piper/piper --model $pm --output_file /tmp/t.wav >/dev/null 2>&1 && test -s /tmp/t.wav'"
step "printf 'VOICE_BACKEND=local\nLD_LIBRARY_PATH=$speech/whisper\nWHISPER_CLI=$speech/whisper/whisper-cli\nWHISPER_DAEMON=$speech/whisper/whisper-daemon\nPIPER_RESIDENT=1\nWHISPER_MODEL=$wm\nPIPER_CLI=$speech/piper/piper\nPIPER_MODEL=$pm\n' | sudo tee $envfile >/dev/null"
restart local
echo "VOICE_OK: local speech ($(basename "$wmodel"), $(basename "$pmodel"))"
