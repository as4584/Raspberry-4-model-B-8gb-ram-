#!/usr/bin/env bash
# First-boot provisioning for Raspberry Pi 4 Model B 8GB, Raspberry Pi OS 64-bit.
# Run ONCE as your normal (non-root) Pi user on the Pi itself:
#   bash /boot/firmware/pi-receptionist/pi_bootstrap.sh
# Re-running is safe: installed steps are skipped and an existing checkout is
# fast-forwarded only. It never reboots, touches firmware/EEPROM, enables
# Tailscale SSH, exposes a port, or stores an auth key. `tailscale up` prints a
# login URL that the OWNER approves in a browser.
# DRY_RUN=1 prints the plan only. BOOTSTRAP_* vars are test-only overrides.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/as4584/Raspberry-4-model-B-8gb-ram-.git}"
TS_HOSTNAME="${TS_HOSTNAME:-lex-receptionist-pi}"
dry="${DRY_RUN:-0}"
uid="${BOOTSTRAP_UID:-$(id -u)}"
arch="${BOOTSTRAP_ARCH:-$(uname -m)}"
os_release="${BOOTSTRAP_OS_RELEASE:-/etc/os-release}"
home="${BOOTSTRAP_HOME:-$HOME}"
checkout="$home/projects/pi-receptionist"
evidence_dir="$home/pi-evidence"

[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user, not root (sudo is used per step)" >&2; exit 20; }
[[ "$arch" == "aarch64" ]] || { echo "FAIL: 64-bit Raspberry Pi OS (aarch64) required, got $arch" >&2; exit 21; }
# shellcheck source=/dev/null  # os-release path is overridable for tests
codename="$(. "$os_release" && echo "${VERSION_CODENAME:-}")"
[[ -n "$codename" ]] || { echo "FAIL: VERSION_CODENAME missing from $os_release" >&2; exit 22; }

if [[ "$dry" != "1" ]]; then
  mkdir -p "$evidence_dir"
  log="$evidence_dir/bootstrap-$(date -u +%Y%m%dT%H%M%SZ).log"
  exec > >(tee -a "$log") 2>&1
else
  log="$evidence_dir/bootstrap-DRYRUN.log"
fi
echo "evidence_log=$log"
echo "codename=$codename arch=$arch"

step() {  # step "<shell command>"
  echo "PLAN: $1"
  [[ "$dry" == "1" ]] || bash -c "$1"
}
have() { [[ "$dry" != "1" ]] && command -v "$1" >/dev/null 2>&1; }

export DEBIAN_FRONTEND=noninteractive
# The Pi has no RTC: first boot starts at the image build date and apt rejects
# repo signatures until NTP corrects the clock. Wait up to 10 min.
step "for i in \$(seq 120); do [ \"\$(timedatectl show -p NTPSynchronized --value)\" = yes ] && break; sleep 5; done; date -u"
echo "== 1/6 base packages"
step "sudo apt-get update"
step "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git python3-venv python3-pip curl"

echo "== 2/6 tailscale (official signed apt repo)"
if have tailscale; then
  echo "tailscale already installed: $(tailscale version | head -1)"
else
  ts="https://pkgs.tailscale.com/stable/debian/$codename"
  step "sudo mkdir -p --mode=0755 /usr/share/keyrings"
  step "curl -fsSL $ts.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null"
  step "curl -fsSL $ts.tailscale-keyring.list | sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null"
  step "sudo apt-get update"
  step "sudo apt-get install -y tailscale"
fi

echo "== 3/6 tailscale login"
# Unattended: a one-off pre-auth key staged on the USB is moved here (root, 600)
# by firstboot_install.sh, passed by file reference, then destroyed.
authkey="${TS_AUTHKEY_FILE:-/etc/pi-receptionist/tailscale.authkey}"
if [[ "${SKIP_TAILSCALE_UP:-0}" == "1" ]]; then
  echo "SKIP_TAILSCALE_UP=1: run later: sudo tailscale up --hostname=$TS_HOSTNAME"
elif have tailscale && [[ -n "$(tailscale ip -4 2>/dev/null || true)" ]]; then
  echo "tailscale already connected"
elif [[ -e "$authkey" ]]; then
  step "sudo tailscale up --hostname=$TS_HOSTNAME --auth-key=file:$authkey"
  step "sudo shred -u $authkey"
elif [[ "${NONINTERACTIVE:-0}" == "1" ]]; then
  echo "BLOCKER: no Tailscale auth key staged; owner runs: sudo tailscale up --hostname=$TS_HOSTNAME"
else
  step "sudo tailscale up --hostname=$TS_HOSTNAME"
fi

echo "== 4/6 repo checkout"
if [[ -d "$checkout/.git" ]]; then
  [[ "$dry" == "1" || -z "$(git -C "$checkout" status --porcelain --untracked-files=no)" ]] \
    || { echo "FAIL: $checkout has local changes; not touching it" >&2; exit 23; }
  step "git -C '$checkout' fetch origin && git -C '$checkout' pull --ff-only"
else
  step "mkdir -p '$home/projects' && git clone '$REPO_URL' '$checkout'"
fi

echo "== 5/6 venv + tests"
step "python3 -m venv '$checkout/.venv'"
step "'$checkout/.venv/bin/python' -m pip install -e '${checkout}[dev]'"
step "cd '$checkout' && .venv/bin/python -m pytest -q"

echo "== 6/6 hardware preflight"
step "cd '$checkout' && bash scripts/pi_preflight.sh"
step "sudo mkdir -p /var/lib/pi-receptionist && sudo touch /var/lib/pi-receptionist/bootstrapped"

echo "BOOTSTRAP_DONE: attach $log (sanitized) to GitHub issue #2"
