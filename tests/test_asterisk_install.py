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
def test_installs_configs_renders_templates_and_starts_service():
    r, p = plan({"TELNYX_SIP_USERNAME": "u", "TELNYX_SIP_PASSWORD": "p"})
    assert r.returncode == 0, r.stderr
    assert "envsubst" in p and "pjsip-secrets.conf.tmpl" in p and "ari.conf.tmpl" in p
    assert "/run/asterisk-secrets/pjsip-secrets.conf" in p               # Telnyx creds: RAM only
    for f in ("pjsip.conf", "rtp.conf", "logger.conf"):
        assert f"deploy/asterisk/{f}" in p
    assert "install -m 640 -o root -g asterisk" in p                  # configs readable by asterisk only
    assert "/etc/pi-receptionist/ari.env" in p and "chmod 600" in p
    assert "systemctl enable --now asterisk.service" in p
    assert p.index("envsubst") < p.index("systemctl enable")


@needs_bash
def test_without_telnyx_creds_installs_sipp_only_and_says_so():
    r, p = plan({"TELNYX_SIP_USERNAME": "", "TELNYX_SIP_PASSWORD": ""})
    assert r.returncode == 0, r.stderr
    assert "SIPP_ONLY" in r.stdout
    assert "pjsip-secrets" not in p                                    # nothing rendered; #tryinclude skips


@needs_bash
def test_refuses_root():
    r, _ = plan(uid="0")
    assert r.returncode == 61


def test_unit_runs_unprivileged_and_restarts():
    u = UNIT.read_text()
    assert "-U asterisk -G asterisk" in u and "Restart=on-failure" in u
    assert "NoNewPrivileges=yes" in u and "ProtectHome=yes" in u


@needs_bash
def test_secrets_never_appear_in_output():
    r, _ = plan({"TELNYX_SIP_USERNAME": "user-xyz", "TELNYX_SIP_PASSWORD": "S3cr3t-Value-91"})
    assert "S3cr3t-Value-91" not in r.stdout + r.stderr
