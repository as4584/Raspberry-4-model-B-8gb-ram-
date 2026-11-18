#!/usr/bin/env bash
# Build the resident whisper against a whisper.cpp build tree (pinned v1.9.5 on the Pi).
#   bash native/build_whisper_daemon.sh ~/opt/whisper.cpp-v1.9.5 [out-dir]
set -euo pipefail
src="${1:?whisper.cpp source dir}"; out="${2:-$src/build/bin}"
here="$(cd "$(dirname "$0")" && pwd)"
g++ -O3 -std=c++17 "$here/whisper_daemon.cpp" -I"$src/include" -I"$src/ggml/include" \
  -L"$src/build/bin" -L"$src/build/src" -L"$src/build/ggml/src" -lwhisper -lggml -lggml-base \
  -Wl,-rpath,'$ORIGIN' -o "$out/whisper-daemon"
echo "BUILT $out/whisper-daemon"
