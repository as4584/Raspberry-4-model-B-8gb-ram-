#!/usr/bin/env bash
# S4: install pinned whisper.cpp + Piper and speech models in user space on the Pi.
# Refuses to run while the Pi reports under-voltage/throttling. Verifies SHA256 of
# every download. Usage: scripts/pi_install_speech.sh   (logs to ~/opt/build-logs/)
set -euo pipefail

WHISPER_TAG=v1.9.5
WHISPER_COMMIT=d1be6fde11ac6e0407606b4e42fe72d34add8037
PIPER_TAG=2023.11.14-2
PIPER_TGZ_SHA=fea0fd2d87c54dbc7078d0f878289f404bd4d6eea6e7444a77835d1537ab88eb
HF_WHISPER=https://huggingface.co/ggerganov/whisper.cpp/resolve/main
HF_PIPER=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium

OPT="$HOME/opt"; MODELS="$HOME/models"; LOGS="$OPT/build-logs"
mkdir -p "$OPT" "$MODELS/whisper" "$MODELS/piper" "$LOGS"

throttled=$(vcgencmd get_throttled | cut -d= -f2)
if [ "$((throttled & 0xF))" -ne 0 ] && [ "${ALLOW_THROTTLED:-0}" != 1 ]; then
  echo "REFUSING: get_throttled=$throttled (under-voltage/throttling now). Fix the PSU or set ALLOW_THROTTLED=1 (installs only)." >&2
  exit 3
fi

fetch() { # url dest sha256
  if [ ! -f "$2" ]; then curl -fL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"; fi
  echo "$3  $2" | sha256sum -c -
}

fetch "$HF_WHISPER/ggml-tiny.en.bin" "$MODELS/whisper/ggml-tiny.en.bin" 921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f
fetch "$HF_WHISPER/ggml-base.en.bin" "$MODELS/whisper/ggml-base.en.bin" a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002
fetch "$HF_WHISPER/ggml-base.en-q5_1.bin" "$MODELS/whisper/ggml-base.en-q5_1.bin" 4baf70dd0d7c4247ba2b81fafd9c01005ac77c2f9ef064e00dcf195d0e2fdd2f
fetch "$HF_PIPER/en_US-lessac-medium.onnx" "$MODELS/piper/en_US-lessac-medium.onnx" 5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f
fetch "$HF_PIPER/en_US-lessac-medium.onnx.json" "$MODELS/piper/en_US-lessac-medium.onnx.json" efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0

fetch "https://github.com/rhasspy/piper/releases/download/$PIPER_TAG/piper_linux_aarch64.tar.gz" "$OPT/piper_linux_aarch64.tar.gz" "$PIPER_TGZ_SHA"
[ -x "$OPT/piper/piper" ] || tar -xzf "$OPT/piper_linux_aarch64.tar.gz" -C "$OPT"

src="$OPT/whisper.cpp-$WHISPER_TAG"
[ -d "$src" ] || git clone --depth 1 --branch "$WHISPER_TAG" https://github.com/ggml-org/whisper.cpp "$src"
[ "$(git -C "$src" rev-parse HEAD)" = "$WHISPER_COMMIT" ] || { echo "whisper.cpp commit mismatch" >&2; exit 4; }
cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF >"$LOGS/whisper-cmake.log" 2>&1
cmake --build "$src/build" -j 3 --target whisper-cli >"$LOGS/whisper-build.log" 2>&1

sha256sum "$src/build/bin/whisper-cli" "$OPT/piper/piper"
echo "WHISPER_CLI=$src/build/bin/whisper-cli"
echo "PIPER_CLI=$OPT/piper/piper"
