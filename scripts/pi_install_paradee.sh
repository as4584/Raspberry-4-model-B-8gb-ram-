#!/usr/bin/env bash
# Install the Paradee voice under /opt (the service has ProtectHome=yes, so nothing may live
# in a home dir) and switch voice.env to it. Piper settings stay in voice.env: rollback is
#   sudo sed -i '/^TTS_ENGINE=/d' /etc/pi-receptionist/voice.env && sudo systemctl restart pi-receptionist
#
#   bash scripts/pi_install_paradee.sh <uv-binary> <paradee_int8.onnx> <config.json>
set -euo pipefail
UV="$1"; MODEL="$2"; CONFIG="$3"
P=/opt/pi-receptionist/speech/paradee
PARADEE_REF=2de547e                      # pinned commit of github.com/sahilmahendrakar/paradee
SPACY_WHL=https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
sudo install -d -m 755 "$P" "$P/model/onnx"
sudo env UV_PYTHON_INSTALL_DIR="$P/python" "$UV" venv -q --python-preference only-managed -p 3.12 "$P/venv"
sudo env VIRTUAL_ENV="$P/venv" UV_PYTHON_INSTALL_DIR="$P/python" "$UV" pip install -q \
  "git+https://github.com/sahilmahendrakar/paradee@$PARADEE_REF" soundfile "$SPACY_WHL"
sudo install -m 644 "$MODEL" "$P/model/onnx/paradee_int8.onnx"
sudo install -m 644 "$CONFIG" "$P/model/config.json"
sudo chmod -R a+rX "$P"
# Smoke test as the service user, with no usable home, exactly like the sandbox.
SCRIPT="$(cd "$(dirname "$0")" && pwd)/paradee_daemon.py"
OUT=$(sudo -u receptionist mktemp -d)
WAV=$(echo "Hello, thanks for calling." | sudo -u receptionist env HOME=/nonexistent "$P/venv/bin/python" \
  /opt/pi-receptionist/app/scripts/paradee_daemon.py --model "$P/model/onnx/paradee_int8.onnx" --output_dir "$OUT" 2>/dev/null)
sudo -u receptionist test -s "$WAV" && echo "PARADEE_SMOKE_OK $WAV" || { echo "PARADEE_SMOKE_FAILED"; exit 1; }
E=/etc/pi-receptionist/voice.env
sudo sed -i '/^TTS_ENGINE=/d;/^PARADEE_/d' "$E"
printf 'TTS_ENGINE=paradee\nPARADEE_PYTHON=%s\nPARADEE_MODEL=%s\nPARADEE_VOICE=af_heart\nPARADEE_THREADS=2\n' \
  "$P/venv/bin/python" "$P/model/onnx/paradee_int8.onnx" | sudo tee -a "$E" >/dev/null
echo "PARADEE_INSTALLED"
