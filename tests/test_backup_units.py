"""S8: nightly backup runs as a hardened oneshot + timer, installed with the service."""
import configparser
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SVC = ROOT / "deploy" / "pi-receptionist-backup.service"
TMR = ROOT / "deploy" / "pi-receptionist-backup.timer"
BASH = shutil.which("bash")


def ini(p):
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(p.read_text())
    return cp


def test_backup_service_is_hardened_oneshot():
    s = ini(SVC)["Service"]
    assert s["Type"] == "oneshot" and s["User"] == "receptionist"
    assert "-m receptionist.backup backup /var/lib/pi-receptionist/data/receptionist.sqlite /var/backups/pi-receptionist" in s["ExecStart"]
    assert "--keep 14" in s["ExecStart"]
    assert s["ProtectSystem"] == "strict" and s["NoNewPrivileges"] == "yes"
    assert set(s["ReadWritePaths"].split()) == {"/var/lib/pi-receptionist/data", "/var/backups/pi-receptionist"}


def test_timer_is_nightly_and_catches_up_after_power_loss():
    t = ini(TMR)["Timer"]
    assert t["OnCalendar"] == "*-*-* 03:30:00" and t["Persistent"] == "true"
    assert ini(TMR)["Install"]["WantedBy"] == "timers.target"


@pytest.mark.skipif(BASH is None, reason="bash required")
def test_installer_installs_backup_dir_and_timer():
    env = {**os.environ, "DRY_RUN": "1", "BOOTSTRAP_UID": "1000"}
    out = subprocess.run([BASH, str(ROOT / "scripts" / "pi_install_service.sh"), "abc1234"], env=env,
                         capture_output=True, text=True, timeout=30).stdout
    assert "install -d -o receptionist -g receptionist -m 750 /var/backups/pi-receptionist" in out
    assert "pi-receptionist-backup.timer" in out and "systemctl enable --now pi-receptionist-backup.timer" in out
