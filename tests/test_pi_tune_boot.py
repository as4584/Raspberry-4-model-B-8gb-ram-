"""Gate for scripts/pi_tune_boot.sh (faster boot after first-boot provisioning).

Measured on the PI4 2026-11-17: 71 s boot; NetworkManager-wait-online 29 s
(pulled in only by cloud-init + finished first-boot service), man-db 29 s,
cloud-init ~10 s on the critical chain every boot. FIXTURE evidence only;
the real before/after numbers come from systemd-analyze on the Pi."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pi_tune_boot.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash required")


def run(tmp_path, *args, marker=True, uid="1000"):
    m = tmp_path / "bootstrapped"
    if marker:
        m.write_text("")
    env = {**os.environ, "DRY_RUN": "1", "TUNE_MARKER": str(m), "BOOTSTRAP_UID": uid}
    r = subprocess.run([BASH, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)
    return r, "\n".join(l[6:] for l in r.stdout.splitlines() if l.startswith("PLAN: "))


def test_apply_plan_targets_measured_bottlenecks(tmp_path):
    r, p = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "touch /etc/cloud/cloud-init.disabled" in p
    assert "nm-online -s -q --timeout=10" in p
    assert "systemctl mask man-db.timer man-db.service" in p
    assert "/var/lib/man-db/auto-update" in p
    assert "systemctl disable --now bluetooth.service" in p
    assert "systemctl daemon-reload" in p


def test_refuses_before_first_boot_provisioning_finished(tmp_path):
    """cloud-init must keep running until the first-boot service has succeeded."""
    r, _ = run(tmp_path, marker=False)
    assert r.returncode == 50 and "FAIL:" in r.stderr


def test_refuses_root(tmp_path):
    r, _ = run(tmp_path, uid="0")
    assert r.returncode == 51 and "FAIL:" in r.stderr


def test_revert_undoes_every_change(tmp_path):
    r, p = run(tmp_path, "--revert")
    assert r.returncode == 0, r.stderr
    assert "rm -f /etc/cloud/cloud-init.disabled" in p
    assert "rm -f /etc/systemd/system/NetworkManager-wait-online.service.d/10-timeout.conf" in p
    assert "systemctl unmask man-db.timer man-db.service" in p
    assert "systemctl enable bluetooth.service" in p


@pytest.mark.parametrize("forbidden", ["config.txt", "rpi-eeprom", "reboot", "cmdline.txt",
                                       "ssh.service", "tailscaled", "NetworkManager.service"])
def test_never_touches_boot_firmware_or_remote_access(tmp_path, forbidden):
    for args in ((), ("--revert",)):
        _, p = run(tmp_path, *args)
        assert forbidden not in p, (args, forbidden)
