"""S1 gate: unattended first boot (plug in -> provisioned, no human at the Pi).

FIXTURE evidence only; real proof is the Pi's ~/pi-evidence log on issue #2.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "pi_bootstrap.sh"
INSTALL = ROOT / "scripts" / "firstboot_install.sh"
STAGER = ROOT / "scripts" / "stage_bootfs.py"
BASH = shutil.which("bash")
SECRET = "tskey-auth-FAKE0000000000-FIXTURE"
needs_bash = pytest.mark.skipif(BASH is None, reason="bash required")


def bootstrap(tmp_path, extra):
    osr = tmp_path / "os-release"
    osr.write_text("VERSION_CODENAME=bookworm\n", newline="\n")
    env = {**os.environ, "DRY_RUN": "1", "BOOTSTRAP_OS_RELEASE": str(osr),
           "BOOTSTRAP_ARCH": "aarch64", "BOOTSTRAP_UID": "1000",
           "BOOTSTRAP_HOME": str(tmp_path / "home"), **extra}
    return subprocess.run([BASH, str(BOOTSTRAP)], env=env, capture_output=True,
                          text=True, timeout=30)


@needs_bash
def test_authkey_file_is_used_by_reference_and_destroyed(tmp_path):
    key = tmp_path / "tailscale.authkey"
    key.write_text(SECRET)
    r = bootstrap(tmp_path, {"TS_AUTHKEY_FILE": str(key)})
    assert r.returncode == 0, r.stderr
    assert f"--auth-key=file:{key}" in r.stdout
    assert f"shred -u {key}" in r.stdout
    assert SECRET not in r.stdout + r.stderr


@needs_bash
def test_noninteractive_without_key_skips_login_and_reports_blocker(tmp_path):
    r = bootstrap(tmp_path, {"NONINTERACTIVE": "1",
                             "TS_AUTHKEY_FILE": str(tmp_path / "absent")})
    assert r.returncode == 0, r.stderr
    assert "tailscale up" not in r.stdout.replace("run later: sudo tailscale up", "")
    assert "BLOCKER:" in r.stdout


@needs_bash
def test_bootstrap_marks_done_only_at_the_end(tmp_path):
    r = bootstrap(tmp_path, {})
    lines = [l for l in r.stdout.splitlines() if l.startswith("PLAN: ")]
    assert "/var/lib/pi-receptionist/bootstrapped" in lines[-1]


@needs_bash
def test_install_creates_user_service_and_moves_key(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "tailscale.authkey").write_text(SECRET)
    env = {**os.environ, "DRY_RUN": "1", "FIRSTBOOT_KIT": str(kit),
           "FIRSTBOOT_USER": "alex"}
    r = subprocess.run([BASH, str(INSTALL)], env=env, capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "User=alex" in out
    assert "ConditionPathExists=!/var/lib/pi-receptionist/bootstrapped" in out
    assert "After=network-online.target" in out
    assert "NONINTERACTIVE=1" in out
    assert "systemctl enable pi-receptionist-firstboot.service" in out
    assert "install -m 600" in out and "/etc/pi-receptionist/tailscale.authkey" in out
    assert f"shred -u {kit}/tailscale.authkey" in out
    assert SECRET not in out + r.stderr


@needs_bash
def test_install_refuses_without_a_normal_user(tmp_path):
    env = {**os.environ, "DRY_RUN": "1", "FIRSTBOOT_KIT": str(tmp_path),
           "FIRSTBOOT_USER": ""}
    r = subprocess.run([BASH, str(INSTALL)], env=env, capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 30 and "FAIL:" in r.stderr


def bootfs(tmp_path, extra=None):
    b = tmp_path / "bootfs"
    b.mkdir()
    (b / "config.txt").write_text("arm_64bit=1\n")
    (b / "cmdline.txt").write_text("console=tty1\n")
    for name, text in (extra or {}).items():
        (b / name).write_text(text, newline="\n")
    return b


def stage(*args):
    return subprocess.run([sys.executable, str(STAGER), *map(str, args)],
                          capture_output=True, text=True, timeout=30)


FIRSTRUN = "#!/bin/bash\nset +e\nuseradd alex\nrm -f /boot/firstrun.sh\nexit 0\n"


def test_autorun_hooks_imager_firstrun_before_self_delete_once(tmp_path):
    b = bootfs(tmp_path, {"firstrun.sh": FIRSTRUN})
    assert stage(b, "--autorun").returncode == 0
    assert stage(b, "--autorun").returncode == 0  # idempotent
    lines = (b / "firstrun.sh").read_text().splitlines()
    hook = [i for i, l in enumerate(lines) if "firstboot_install.sh" in l]
    rm = [i for i, l in enumerate(lines) if l.startswith("rm -f /boot")]
    assert len(hook) == 1 and hook[0] < rm[0]
    assert lines.index("useradd alex") < hook[0]


def test_autorun_hooks_cloud_init_runcmd(tmp_path):
    ud = "#cloud-config\nhostname: x\nruncmd:\n  - [rfkill, unblock, wifi]\n"
    b = bootfs(tmp_path, {"user-data": ud})
    assert stage(b, "--autorun").returncode == 0
    assert stage(b, "--autorun").returncode == 0
    text = (b / "user-data").read_text()
    assert text.count("runcmd:") == 1
    assert text.count("firstboot_install.sh") == 1
    assert "  - [rfkill, unblock, wifi]" in text


def test_autorun_without_imager_customisation_fails(tmp_path):
    r = stage(bootfs(tmp_path), "--autorun")
    assert r.returncode != 0 and "customisation" in r.stderr


def test_authkey_staged_privately_and_never_printed(tmp_path):
    src = tmp_path / "ts.key"
    src.write_text(SECRET + "\n")
    b = bootfs(tmp_path)
    r = stage(b, "--tailscale-authkey-file", src)
    assert r.returncode == 0, r.stderr
    kit = b / "pi-receptionist"
    assert (kit / "tailscale.authkey").read_text() == SECRET
    assert SECRET not in r.stdout + r.stderr
    assert "tailscale.authkey" not in (kit / "MANIFEST.sha256").read_text()
