#!/usr/bin/env bash
# S3 smoke: build pinned llama.cpp on the Pi, fetch one Apache-2.0 GGUF (SHA256-checked),
# run llama-bench (prompt 64 tok, generate 32 tok, 5 reps) and record peak RSS.
# Smoke only: no accuracy claims, not wired into booking.
set -euo pipefail

LLAMA_TAG=v1.0.0
LLAMA_COMMIT=b1b770d90d94cad9e9ea5b993bdbc383a522fb28
GGUF_URL=https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/9217f5db79a29953eb74d5343926648285ec7e67/qwen2.5-0.5b-instruct-q4_k_m.gguf
GGUF_SHA=74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db

OPT="$HOME/opt"; LOGS="$OPT/build-logs"; GGUF="$HOME/models/llm/qwen2.5-0.5b-instruct-q4_k_m.gguf"
mkdir -p "$LOGS" "$(dirname "$GGUF")"

if [ ! -f "$GGUF" ]; then curl -fL --retry 3 -o "$GGUF.part" "$GGUF_URL" && mv "$GGUF.part" "$GGUF"; fi
echo "$GGUF_SHA  $GGUF" | sha256sum -c -

src="$OPT/llama.cpp-$LLAMA_TAG"
[ -d "$src" ] || git clone --depth 1 --branch "$LLAMA_TAG" https://github.com/ggml-org/llama.cpp "$src"
[ "$(git -C "$src" rev-parse HEAD)" = "$LLAMA_COMMIT" ] || { echo "llama.cpp commit mismatch" >&2; exit 4; }
if [ ! -x "$src/build/bin/llama-bench" ]; then
  cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF >"$LOGS/llama-cmake.log" 2>&1
  cmake --build "$src/build" -j 3 --target llama-bench >"$LOGS/llama-build.log" 2>&1
fi

echo "throttled_before=$(vcgencmd get_throttled) $(vcgencmd measure_temp)"
"$src/build/bin/llama-bench" -m "$GGUF" -t 4 -p 64 -n 32 -r 5 -o jsonl &
pid=$!; peak=0
while kill -0 "$pid" 2>/dev/null; do
  rss=$(awk '/VmRSS/{print $2}' "/proc/$pid/status" 2>/dev/null || echo 0)
  [ "${rss:-0}" -gt "$peak" ] && peak=$rss
  sleep 0.5
done
wait "$pid"
echo "peak_rss_kb=$peak"
echo "throttled_after=$(vcgencmd get_throttled) $(vcgencmd measure_temp)"
sha256sum "$src/build/bin/llama-bench"
