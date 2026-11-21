"""T2: install our Asterisk config + service. Templates are rendered with envsubst from
the environment (Doppler in production); missing Telnyx creds still allow a SIPp-only
install; the ARI password is generated once into a root-only file, never git."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pi_install_asterisk.sh"
UNIT = ROOT / "deploy" / "asterisk.service"
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash required")


def plan(env=None, uid="1000"):
    e = {**os.environ, "DRY_RUN": "1", "BOOTSTRAP_UID": uid, **(env or {})}
    e.pop("TELNYX_SIP_USERNAME", None) if env is None else None
    r = subprocess.run([BASH, str(SCRIPT)], env=e, capture_output=True, text=True, timeout=30)
    return r, "\n".join(l[6:] for l in r.stdout.splitlines() if l.startswith("PLAN: "))


@needs_bash
def test_installs_static_config_templates_and_doppler_secrets_service():
    r, p = plan()
    assert r.returncode == 0, r.stderr
    for f in ("pjsip.conf", "rtp.conf", "logger.conf", "ari.conf", "extensions.conf"):
        assert f"deploy/asterisk/{f}" in p
    assert "install -m 640 -o root -g asterisk" in p                  # configs readable by asterisk only
    for t in ("pjsip-secrets.conf.tmpl", "ari-users.conf.tmpl"):
        assert f"/opt/pi-receptionist/templates/" in p and t in p
    assert "deploy/pi-receptionist-secrets.service" in p
    assert "systemctl enable --now pi-receptionist-secrets.service" in p
    assert p.index("pi-receptionist-secrets.service") < p.index("systemctl restart asterisk.service")
    assert "envsubst" not in p                                         # rendering only happens in RAM at boot


@needs_bash
def test_installer_never_handles_secret_values():
    r, p = plan({"TELNYX_SIP_USERNAME": "user-xyz", "TELNYX_SIP_PASSWORD": "S3cr3t-Value-91"})
    assert "S3cr3t-Value-91" not in r.stdout + r.stderr and "user-xyz" not in p
    assert "tee /etc/pi-receptionist/ari.env" not in p and "/dev/urandom" not in p   # ARI password: Doppler
    assert "rm -f /etc/asterisk/ari.conf.rendered /etc/pi-receptionist/ari.env" in p   # old on-disk copy removed


@needs_bash
def test_refuses_root():
    r, _ = plan(uid="0")
    assert r.returncode == 61


def test_unit_runs_unprivileged_and_restarts():
    u = UNIT.read_text()
    assert "-U asterisk -G asterisk" in u and "Restart=on-failure" in u
    assert "NoNewPrivileges=yes" in u and "ProtectHome=yes" in u


