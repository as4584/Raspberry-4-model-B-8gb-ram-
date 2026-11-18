"""Gate for the Pi Doppler helpers. A stub `doppler` records its args/env so we
can prove the token is stored privately, never echoed, and only injected via
DOPPLER_TOKEN. FIXTURE evidence only."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "pi_doppler_token.sh"
WRAP = ROOT / "scripts" / "with_secrets.sh"
BASH = shutil.which("bash")
TOKEN = "dp.st." + "prd." + "FIXTUREfixture0123456789abcdefFIXTURE"

pytestmark = pytest.mark.skipif(BASH is None, reason="bash required")


def env_for(tmp_path, **extra):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "doppler"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "ARGS:$*" >> "{(tmp_path / "doppler.log").as_posix()}"\n'
        f'[ -n "$DOPPLER_TOKEN" ] && echo "HAS_TOKEN" >> "{(tmp_path / "doppler.log").as_posix()}"\n'
        'if [ "$1" = secrets ]; then echo TWILIO_ACCOUNT_SID; echo TWILIO_AUTH_TOKEN; fi\n'
        'if [ "$1" = run ]; then shift; [ "$1" = -- ] && shift; exec "$@"; fi\n',
        newline="\n")
    stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "PI_SECRETS_DIR": str(tmp_path / "secrets"), "BOOTSTRAP_UID": "1000", **extra}


def setup(tmp_path, token=TOKEN, **extra):
    return subprocess.run([BASH, str(SETUP)], input=token + "\n", env=env_for(tmp_path, **extra),
                          capture_output=True, text=True, timeout=30)


def test_token_stored_private_and_never_echoed(tmp_path):
    r = setup(tmp_path)
    assert r.returncode == 0, r.stderr
    f = tmp_path / "secrets" / "doppler.env"
    assert f.read_text().strip() == f"DOPPLER_TOKEN={TOKEN}"
    if os.name != "nt":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert TOKEN not in r.stdout + r.stderr
    log = (tmp_path / "doppler.log").read_text()
    assert TOKEN not in log            # never passed as an argument
    assert "HAS_TOKEN" in log          # passed via environment instead
    assert "--only-names" in log       # verification lists names, not values
    assert "TWILIO_AUTH_TOKEN" in r.stdout


@pytest.mark.parametrize("bad", ["", "not-a-token", "dp.pt.personal" + "x" * 30])
def test_rejects_non_service_tokens(tmp_path, bad):
    r = setup(tmp_path, token=bad)
    assert r.returncode != 0 and "FAIL:" in r.stderr
    assert not (tmp_path / "secrets" / "doppler.env").exists()


def test_refuses_root(tmp_path):
    r = setup(tmp_path, BOOTSTRAP_UID="0")
    assert r.returncode != 0 and "FAIL:" in r.stderr


def test_wrapper_injects_token_via_env_and_runs_command(tmp_path):
    assert setup(tmp_path).returncode == 0
    r = subprocess.run([BASH, str(WRAP), "echo", "ran-ok"], env=env_for(tmp_path),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "ran-ok" in r.stdout
    log = (tmp_path / "doppler.log").read_text()
    assert "ARGS:run -- echo ran-ok" in log and TOKEN not in log


def test_wrapper_fails_clearly_without_token(tmp_path):
    r = subprocess.run([BASH, str(WRAP), "echo", "x"], env=env_for(tmp_path),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "pi_doppler_token.sh" in r.stderr
