"""S8: the receptionist runs as a hardened systemd service on the Pi.
Pins least privilege, loopback-only binding, crash restart and the
installer's plan. FIXTURE evidence; PI4 evidence is the live unit."""
import configparser
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy" / "pi-receptionist.service"
INSTALL = ROOT / "scripts" / "pi_install_service.sh"
BASH = shutil.which("bash")


def unit():
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(UNIT.read_text())
    return cp


def env_lines():
    return [l.split("=", 1)[1] for l in UNIT.read_text().splitlines() if l.startswith("Environment=")]


def test_runs_as_dedicated_unprivileged_user():
    s = unit()["Service"]
    assert s["User"] == "receptionist" and s["Group"] == "receptionist"
    assert s["NoNewPrivileges"] == "yes"
    assert s["CapabilityBoundingSet"] == ""


def test_binds_loopback_only_and_production_mode():
    s = unit()["Service"]
    assert "--host 127.0.0.1" in s["ExecStart"]
    assert "0.0.0.0" not in UNIT.read_text()
    assert "DEV_ALLOW_UNSIGNED=0" in env_lines()


def test_filesystem_is_read_only_except_data():
    s = unit()["Service"]
    assert s["ProtectSystem"] == "strict" and s["ProtectHome"] == "yes" and s["PrivateTmp"] == "yes"
    assert s["ReadWritePaths"] == "/var/lib/pi-receptionist/data"
    assert "RECEPTIONIST_DB=/var/lib/pi-receptionist/data/receptionist.sqlite" in env_lines()


def test_restarts_on_crash_and_stops_gracefully():
    s = unit()["Service"]
    assert s["Restart"] == "on-failure"
    assert s["KillSignal"] == "SIGTERM" and int(s["TimeoutStopSec"]) <= 30


def test_bounded_memory_on_8gb_pi():
    assert unit()["Service"]["MemoryMax"].endswith(("M", "G"))


def test_no_secrets_in_unit():
    text = UNIT.read_text()
    assert "TWILIO_AUTH_TOKEN=" not in text and "DOPPLER_TOKEN=" not in text


def test_starts_at_boot():
    assert unit()["Install"]["WantedBy"] == "multi-user.target"


def plan(tmp_path, *args, uid="1000"):
    env = {**os.environ, "DRY_RUN": "1", "BOOTSTRAP_UID": uid}
    r = subprocess.run([BASH, str(INSTALL), *args], env=env, capture_output=True, text=True, timeout=30)
    return r, "\n".join(l[6:] for l in r.stdout.splitlines() if l.startswith("PLAN: "))


@pytest.mark.skipif(BASH is None, reason="bash required")
def test_install_plan(tmp_path):
    r, p = plan(tmp_path, "abc1234")
    assert r.returncode == 0, r.stderr
    assert "useradd --system --no-create-home --shell /usr/sbin/nologin receptionist" in p
    assert "git -C /opt/pi-receptionist/app checkout --detach abc1234" in p
    assert "chown -R root:root /opt/pi-receptionist/app" in p          # code not writable by service
    assert "install -d -o receptionist -g receptionist -m 750 /var/lib/pi-receptionist/data" in p
    assert "systemctl enable --now pi-receptionist.service" in p
    assert "curl -fsS http://127.0.0.1:8000/healthz" in p
    assert p.index("useradd") < p.index("systemctl enable")


@pytest.mark.skipif(BASH is None, reason="bash required")
def test_install_requires_pinned_sha_and_non_root(tmp_path):
    r, _ = plan(tmp_path)
    assert r.returncode == 60 and "FAIL:" in r.stderr
    r, _ = plan(tmp_path, "abc1234", uid="0")
    assert r.returncode == 61 and "FAIL:" in r.stderr


@pytest.mark.skipif(BASH is None, reason="bash required")
def test_uninstall_keeps_booking_data(tmp_path):
    r, p = plan(tmp_path, "--uninstall")
    assert r.returncode == 0, r.stderr
    assert "systemctl disable --now pi-receptionist.service" in p
    assert "/var/lib/pi-receptionist/data" not in p    # never deletes bookings
