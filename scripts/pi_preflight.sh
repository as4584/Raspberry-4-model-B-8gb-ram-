#!/usr/bin/env bash
# Read-only checks. Execute only on the actual Raspberry Pi 4 Model B with 8 GB RAM.
# PREFLIGHT_ROOT is a TEST-ONLY override for /proc fixtures; any run using it
# is labeled evidence_kind=FIXTURE and is never Pi hardware evidence.
set -euo pipefail
root="${PREFLIGHT_ROOT:-}"
if [[ -n "$root" ]]; then evidence_kind=FIXTURE; else evidence_kind=PI4_CANDIDATE; fi
printf 'evidence_kind=%s\n' "$evidence_kind"
printf 'timestamp_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
arch="$(uname -m)"
printf 'architecture=%s\n' "$arch"
[[ "$arch" == "aarch64" ]] || { echo "FAIL: 64-bit ARM64 required" >&2; exit 10; }
[[ -r "$root/proc/device-tree/model" ]] || { echo "FAIL: no physical device tree model" >&2; exit 11; }
model="$(tr -d '\000' <"$root/proc/device-tree/model")"
printf 'model=%s\n' "$model"
[[ "$model" == *"Raspberry Pi 4 Model B"* ]] || { echo "FAIL: not a Pi 4 Model B" >&2; exit 12; }
total_kb="$(awk '/^MemTotal:/{print $2}' "$root/proc/meminfo")"
printf 'mem_total_kb=%s\n' "$total_kb"
[[ "$total_kb" =~ ^[0-9]+$ && "$total_kb" -ge 6800000 ]] || { echo "FAIL: <~8 GB RAM" >&2; exit 13; }
# Power health: bit 0 under-voltage now, bit 2 throttled now, bits 16/18 = since boot.
# Timings taken while the supply sags are not valid evidence.
if command -v vcgencmd >/dev/null; then
  throttled="$(vcgencmd get_throttled)"
  throttled="${throttled#throttled=}"
  printf 'throttled=%s\n' "$throttled"
  t=$((throttled))
  (( (t & 0x5) == 0 )) || { echo "FAIL: power supply under-voltage or CPU throttled right now ($throttled); use the official 5.1V 3A PSU" >&2; exit 19; }
  if (( (t & 0x50000) != 0 )); then echo "power_history=undervoltage_or_throttling_since_boot"; else echo "power_history=clean"; fi
else
  echo "throttled=unknown"
fi
command -v python3 >/dev/null || { echo "FAIL: Python missing" >&2; exit 14; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
  || { echo "FAIL: Python >=3.11 required" >&2; exit 18; }
command -v git >/dev/null || { echo "FAIL: git missing" >&2; exit 15; }
command -v tailscale >/dev/null || { echo "FAIL: tailscale missing" >&2; exit 16; }
[[ -n "$(tailscale ip -4 2>/dev/null || true)" ]] || { echo "FAIL: Tailscale not authenticated/connected" >&2; exit 17; }
printf 'git_sha=%s\n' "$(git rev-parse HEAD 2>/dev/null || echo no-git-checkout)"
echo "PI4_PREFLIGHT_OK: verify both laptops independently via SSH"
