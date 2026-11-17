#!/usr/bin/env bash
# Read-only checks. Execute only on the actual Raspberry Pi 4 Model B with 8 GB RAM.
set -euo pipefail
arch="$(uname -m)"
printf 'architecture=%s\n' "$arch"
[[ "$arch" == "aarch64" ]] || { echo "FAIL: 64-bit ARM64 required" >&2; exit 10; }
[[ -r /proc/device-tree/model ]] || { echo "FAIL: no physical device tree model" >&2; exit 11; }
model="$(tr -d '\000' </proc/device-tree/model)"
printf 'model=%s\n' "$model"
[[ "$model" == *"Raspberry Pi 4 Model B"* ]] || { echo "FAIL: not a Pi 4 Model B" >&2; exit 12; }
total_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo)"
printf 'mem_total_kb=%s\n' "$total_kb"
[[ "$total_kb" =~ ^[0-9]+$ && "$total_kb" -ge 6800000 ]] || { echo "FAIL: <~8 GB RAM" >&2; exit 13; }
command -v python3 >/dev/null || { echo "FAIL: Python missing" >&2; exit 14; }
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python >=3.11 required"'
command -v git >/dev/null || { echo "FAIL: git missing" >&2; exit 15; }
command -v tailscale >/dev/null || { echo "FAIL: tailscale missing" >&2; exit 16; }
[[ -n "$(tailscale ip -4 2>/dev/null || true)" ]] || { echo "FAIL: Tailscale not authenticated/connected" >&2; exit 17; }
printf 'git_sha=%s\n' "$(git rev-parse HEAD 2>/dev/null || echo no-git-checkout)"
echo "PI4_PREFLIGHT_OK: verify both laptops independently via SSH"
