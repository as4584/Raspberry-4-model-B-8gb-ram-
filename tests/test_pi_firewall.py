"""Firewall (owner-approved 2026-11-21, adapted from the kit's ufw block): SSH only via
Tailscale, SIP-TLS 5061 and SRTP 16384-16484 from the internet, nothing else inbound.
Enabled behind a dead-man switch: an automatic `ufw disable` in 5 minutes unless the
operator proves SSH still works and runs --confirm."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pi_firewall.sh"
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash required")


def plan(*args):
    r = subprocess.run([BASH, str(SCRIPT), *args], env={**os.environ, "DRY_RUN": "1", "BOOTSTRAP_UID": "1000"},
                       capture_output=True, text=True, timeout=30)
    return r, [l[6:] for l in r.stdout.splitlines() if l.startswith("PLAN: ")]


@needs_bash
def test_rules_match_the_approved_policy():
    r, p = plan()
    assert r.returncode == 0, r.stderr
    text = "\n".join(p)
    assert "ufw default deny incoming" in text and "ufw default allow outgoing" in text
    assert "ufw allow in on tailscale0 to any port 22 proto tcp" in text
    assert "ufw allow 5061/tcp" in text and "ufw allow 16384:16484/udp" in text
    assert not any("allow 22" in l and "tailscale0" not in l for l in p)     # no LAN/internet SSH
    for closed in ("8000", "8088", "5070", "5060"):
        assert f"allow {closed}" not in text and f"port {closed}" not in text


@needs_bash
def test_dead_man_switch_is_armed_before_enabling():
    _, p = plan()
    arm = next(i for i, l in enumerate(p) if "systemd-run" in l and "ufw disable" in l and "--on-active=300" in l)
    enable = next(i for i, l in enumerate(p) if "ufw --force enable" in l)
    assert arm < enable


@needs_bash
def test_confirm_disarms_the_switch():
    r, p = plan("--confirm")
    assert r.returncode == 0 and any("systemctl stop pi-firewall-rollback.timer" in l for l in p)
