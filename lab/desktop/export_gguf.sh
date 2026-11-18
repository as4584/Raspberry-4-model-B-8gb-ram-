#!/bin/bash
# R5: merged HF model dir -> GGUF F16 -> Q8_0 + Q4_K_M, with SHA256 + sizes. CPU-only; no system packages.
# Usage: export_gguf.sh <merged_dir> <name>
source "$(dirname "$0")/common.sh"
MERGED="$1"; NAME="$2"
LLAMA="$LAB/llama.cpp"            # source checkout: convert_hf_to_gguf.py (pure Python)
BIN="$LAB/llama-bin"              # official prebuilt release binaries: llama-quantize
TAG="${LLAMA_TAG:-}"
if [ ! -d "$LLAMA" ]; then
  git clone --depth 1 ${TAG:+--branch "$TAG"} https://github.com/ggml-org/llama.cpp "$LLAMA"
fi
LLAMA_SHA=$(git -C "$LLAMA" rev-parse HEAD)
if [ ! -x "$BIN/llama-quantize" ]; then
  mkdir -p "$BIN"
  REL="${LLAMA_REL:-b12502}"   # pinned official release (CPU x64 build)
  url="https://github.com/ggml-org/llama.cpp/releases/download/$REL/llama-$REL-bin-ubuntu-x64.tar.gz"
  curl -fsSL "$url" | tar -xz -C "$BIN"
  q=$(find "$BIN" -name llama-quantize -type f | head -1)
  ln -sf "$q" "$BIN/llama-quantize"
  chmod +x "$q"
  echo "$url" > "$BIN/SOURCE_URL"
fi
OUT="$LAB/gguf"; mkdir -p "$OUT"
[ -f "$OUT/$NAME-f16.gguf" ] || "$PY" "$LLAMA/convert_hf_to_gguf.py" "$MERGED" --outtype f16 --outfile "$OUT/$NAME-f16.gguf"
QDIR=$(dirname "$(readlink -f "$BIN/llama-quantize")")
# libgomp is not installed system-wide; reuse the copy bundled in the venv's torch wheel.
TORCH_LIB="$("$PY" -c 'import os, torch; print(os.path.join(os.path.dirname(torch.__file__), "lib"))')"
for q in Q8_0 Q4_K_M; do
  LD_LIBRARY_PATH="$QDIR:$TORCH_LIB:${LD_LIBRARY_PATH:-}" "$BIN/llama-quantize" "$OUT/$NAME-f16.gguf" "$OUT/$NAME-$q.gguf" "$q"
done
{
  echo "llama.cpp convert source commit: $LLAMA_SHA"
  echo "llama-quantize release: $(cat "$BIN/SOURCE_URL")"
  for f in "$OUT/$NAME"-*.gguf; do
    echo "$(sha256sum "$f" | cut -d' ' -f1)  $(stat -c %s "$f")  $(basename "$f")"
  done
} | tee "$OUT/$NAME.SHA256SUMS.txt"
