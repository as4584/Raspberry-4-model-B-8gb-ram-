"""S1 gate for scripts/pi_bootstrap.sh (first-boot provisioning).

DRY_RUN=1 makes the script print its plan instead of executing, so the plan's
safety properties are checked on any host. FIXTURE evidence only.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pi_bootstrap.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash required")


def run(tmp_path, *, codename="bookworm", arch="aarch64", uid="1000", extra=None):
    osr = tmp_path / "os-release"
    osr.write_text(f'ID=debian\nVERSION_CODENAME={codename}\n', newline="\n")
    env = {**os.environ, "DRY_RUN": "1", "BOOTSTRAP_OS_RELEASE": str(osr),
           "BOOTSTRAP_ARCH": arch, "BOOTSTRAP_UID": uid,
           "BOOTSTRAP_HOME": str(tmp_path / "home"), **(extra or {})}
    return subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True,
                          text=True, cwd=ROOT, timeout=30)


def plan(r):
    return [l[len("PLAN: "):] for l in r.stdout.splitlines() if l.startswith("PLAN: ")]


def test_dry_run_plans_full_provisioning_in_order(tmp_path):
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    p = "\n".join(plan(r))
    order = ["apt-get install", "pkgs.tailscale.com/stable/debian/bookworm",
             "apt-get install -y tailscale", "tailscale up --hostname=lex-receptionist-pi",
             "git clone", "-m venv", "pip install -e", "pytest -q", "pi_preflight.sh"]
    idx = [p.find(s) for s in order]
    assert all(i >= 0 for i in idx), dict(zip(order, idx))
    assert idx == sorted(idx), dict(zip(order, idx))


def test_codename_comes_from_os_release(tmp_path):
    r = run(tmp_path, codename="trixie")
    assert r.returncode == 0, r.stderr
    assert "pkgs.tailscale.com/stable/debian/trixie" in "\n".join(plan(r))


@pytest.mark.parametrize("bad", [
    "| sh", "| bash", "0.0.0.0", "DEV_ALLOW_UNSIGNED=1", "--authkey", "sudo pip",
    "--ssh", "reboot", "rpi-eeprom", "overclock",
])
def test_plan_never_contains_unsafe_actions(tmp_path, bad):
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert bad not in "\n".join(plan(r))


def test_refuses_root(tmp_path):
    r = run(tmp_path, uid="0")
    assert r.returncode == 20 and "FAIL:" in r.stderr


@pytest.mark.parametrize("arch", ["x86_64", "armv7l"])
def test_refuses_wrong_arch(tmp_path, arch):
    r = run(tmp_path, arch=arch)
    assert r.returncode == 21 and "FAIL:" in r.stderr


def test_refuses_unknown_codename(tmp_path):
    r = run(tmp_path, codename="")
    assert r.returncode == 22 and "FAIL:" in r.stderr


def test_existing_checkout_is_fast_forwarded_not_recloned(tmp_path):
    (tmp_path / "home" / "projects" / "pi-receptionist" / ".git").mkdir(parents=True)
    p = "\n".join(plan(run(tmp_path)))
    assert "git clone" not in p
    assert "pull --ff-only" in p


def test_skip_tailscale_up_is_honored(tmp_path):
    p = "\n".join(plan(run(tmp_path, extra={"SKIP_TAILSCALE_UP": "1"})))
    assert "tailscale up" not in p
    assert "apt-get install -y tailscale" in p


def test_evidence_log_is_outside_repo(tmp_path):
    r = run(tmp_path)
    log = [l for l in r.stdout.splitlines() if l.startswith("evidence_log=")]
    assert log and "pi-receptionist/" not in log[0] and "pi-evidence" in log[0]


def test_waits_for_clock_sync_before_apt(tmp_path):
    """Pi has no RTC: first boot starts at the image build date, so apt's
    signature checks fail until NTP syncs (observed on the real Pi 2026-11-17)."""
    p = plan(run(tmp_path))
    sync = next(i for i, l in enumerate(p) if "NTPSynchronized" in l)
    apt = next(i for i, l in enumerate(p) if "apt-get update" in l)
    assert sync < apt
