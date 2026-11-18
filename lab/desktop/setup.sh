#!/bin/bash
# Desktop-only (WSL) training env. Never run on the Pi or in CI.
# Self-contained: a private uv binary + uv-managed Python 3.12 inside $LAB; no system packages touched.
set -euo pipefail
LAB="${LAB:-$HOME/pi-model-lab}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$LAB/bin" && cd "$LAB"
if [ ! -x bin/uv ]; then
  curl -fsSL https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz | tar -xz -C bin --strip-components=1
fi
export UV_PYTHON_INSTALL_DIR="$LAB/python" UV_CACHE_DIR="$LAB/uv-cache"
[ -d venv ] || bin/uv venv --python 3.12 venv
VIRTUAL_ENV="$LAB/venv" bin/uv pip install -q -r "$HERE/../requirements-train.txt"
venv/bin/python -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
VIRTUAL_ENV="$LAB/venv" bin/uv pip freeze > freeze.txt
echo SETUP_DONE
