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
  rel=$(curl -fsSL https://api.github.com/repos/ggml-org/llama.cpp/releases/latest)
  url=$(echo "$rel" | grep -o 'https://[^"]*bin-ubuntu-x64.zip' | head -1)
  curl -fsSL "$url" -o "$BIN/rel.zip"
  "$PY" -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$BIN/rel.zip" "$BIN"
  q=$(find "$BIN" -name llama-quantize -type f | head -1)
  ln -sf "$q" "$BIN/llama-quantize"
  chmod +x "$q"
  echo "$url" > "$BIN/SOURCE_URL"
fi
OUT="$LAB/gguf"; mkdir -p "$OUT"
"$PY" "$LLAMA/convert_hf_to_gguf.py" "$MERGED" --outtype f16 --outfile "$OUT/$NAME-f16.gguf"
QDIR=$(dirname "$(readlink -f "$BIN/llama-quantize")")
for q in Q8_0 Q4_K_M; do
  LD_LIBRARY_PATH="$QDIR:${LD_LIBRARY_PATH:-}" "$BIN/llama-quantize" "$OUT/$NAME-f16.gguf" "$OUT/$NAME-$q.gguf" "$q"
done
{
  echo "llama.cpp convert source commit: $LLAMA_SHA"
  echo "llama-quantize release: $(cat "$BIN/SOURCE_URL")"
  for f in "$OUT/$NAME"-*.gguf; do
    echo "$(sha256sum "$f" | cut -d' ' -f1)  $(stat -c %s "$f")  $(basename "$f")"
  done
} | tee "$OUT/$NAME.SHA256SUMS.txt"
