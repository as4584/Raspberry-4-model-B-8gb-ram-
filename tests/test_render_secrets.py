"""Boot-time secrets (adapted from the owner's kit): under `doppler run` with the Pi's
read-only prd token, render Telnyx + ARI credentials into RAM (/run, tmpfs) for Asterisk
and the app. Names only in output; a missing secret skips just its file."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_secrets.sh"
D = ROOT / "deploy"
BASH = shutil.which("bash")
posix = pytest.mark.skipif(BASH is None or os.name == "nt", reason="needs a POSIX bash")
VALUES = {"TELNYX_SIP_USERNAME": "telnyxuser1", "TELNYX_SIP_PASSWORD": "Tpw-Secret-77",
          "ARI_USERNAME": "receptionist", "ARI_PASSWORD": "Ari-Secret-88"}


def render(tmp_path, **env):
    out = {"ASTERISK_SECRETS_DIR": str(tmp_path / "ast"), "APP_SECRETS_DIR": str(tmp_path / "app"),
           "TEMPLATES_DIR": str(D / "asterisk"), "SECRETS_OWNER_GROUP": "", "PATH": os.environ["PATH"]}
    r = subprocess.run([BASH, str(SCRIPT)], env={**out, **env}, capture_output=True, text=True, timeout=30)
    return r, tmp_path / "ast", tmp_path / "app"


@posix
def test_renders_all_files_into_ram_dirs_with_tight_modes(tmp_path):
    r, ast, app = render(tmp_path, **VALUES)
    assert r.returncode == 0, r.stderr
    pj, ari, env = ast / "pjsip-secrets.conf", ast / "ari-users.conf", app / "app.env"
    assert "password = Tpw-Secret-77" in pj.read_text() and "contact_user = telnyxuser1" in pj.read_text()
    assert "[receptionist]" in ari.read_text() and "password = Ari-Secret-88" in ari.read_text()
    assert env.read_text() == "ARI_USERNAME=receptionist\nARI_PASSWORD=Ari-Secret-88\n"
    assert {oct(p.stat().st_mode & 0o777) for p in (pj, ari)} == {"0o640"}
    assert oct(env.stat().st_mode & 0o777) == "0o600"


@posix
def test_output_names_files_never_values(tmp_path):
    r, *_ = render(tmp_path, **VALUES)
    for v in VALUES.values():
        if v != "receptionist":
            assert v not in r.stdout + r.stderr


@posix
def test_missing_secret_skips_only_its_file_and_says_which(tmp_path):
    r, ast, app = render(tmp_path, **{k: v for k, v in VALUES.items() if k != "ARI_PASSWORD"})
    assert r.returncode == 0, r.stderr
    assert (ast / "pjsip-secrets.conf").exists()
    assert not (ast / "ari-users.conf").exists() and not (app / "app.env").exists()
    assert "ARI_PASSWORD" in r.stdout and "missing" in r.stdout


@posix
def test_stale_file_removed_when_secret_disappears(tmp_path):
    render(tmp_path, **VALUES)
    _, ast, _ = render(tmp_path, **{k: v for k, v in VALUES.items() if not k.startswith("TELNYX")})
    assert not (ast / "pjsip-secrets.conf").exists()


def test_ari_conf_is_static_and_includes_ram_users():
    ari = (D / "asterisk" / "ari.conf").read_text()
    assert "#tryinclude /run/asterisk-secrets/ari-users.conf" in ari and "password" not in ari.lower()
    assert not (D / "asterisk" / "ari.conf.tmpl").exists()


def test_secrets_service_runs_under_doppler_with_fallback_before_consumers():
    u = (D / "pi-receptionist-secrets.service").read_text()
    assert "EnvironmentFile=/etc/pi-receptionist/doppler.env" in u
    assert "doppler run --fallback /var/lib/pi-receptionist/secrets/doppler-fallback.json" in u
    assert "Before=asterisk.service pi-receptionist.service" in u and "Type=oneshot" in u
    assert "-- /usr/bin/bash /opt/pi-receptionist/app/scripts/render_secrets.sh" in u   # no exec bit needed
    app = (D / "pi-receptionist.service").read_text()
    assert "EnvironmentFile=-/run/pi-receptionist/app.env" in app
    assert "/etc/pi-receptionist/ari.env" not in app            # no secret on disk any more


def test_server_uses_ari_username_from_environment(monkeypatch):
    from receptionist import server
    assert "ARI_USERNAME" in Path(server.__file__).read_text()


def test_secrets_service_gives_doppler_a_private_home():
    # Pi: "Doppler Error: $HOME is not defined" under the hardened unit.
    u = (D / "pi-receptionist-secrets.service").read_text()
    assert "Environment=HOME=/var/lib/pi-receptionist/secrets" in u
    assert "ProtectHome=yes" in u                          # still can't read real home dirs
