"""Install a read-only, prd-only Doppler service token on the Pi (adapted from the
owner's kit 03-pi-install-token.sh). The token travels only over ssh stdin; a failed
or empty mint must never overwrite the Pi's existing token file."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pi_doppler_service_token.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or os.name == "nt", reason="needs a POSIX bash")
TOKEN = "dp.st.prd.FAKEtokenVALUE1234567890"


def fakes(tmp_path, mint_output, mint_exit=0):
    bin_ = tmp_path / "bin"; bin_.mkdir()
    (bin_ / "doppler").write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/doppler.args"\nprintf "%s" "{mint_output}"\nexit {mint_exit}\n')
    (bin_ / "ssh").write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/ssh.args"\ncat > "{tmp_path}/ssh.stdin"\n')
    for f in bin_.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}


def run(tmp_path, env):
    return subprocess.run([BASH, str(SCRIPT), "prototype@100.92.161.12"], env=env, capture_output=True, text=True,
                          timeout=30)


def test_mints_read_only_prd_token_and_sends_it_only_on_stdin(tmp_path):
    r = run(tmp_path, fakes(tmp_path, TOKEN))
    assert r.returncode == 0, r.stderr
    args = (tmp_path / "doppler.args").read_text()
    for part in ("configs tokens create", "--project lexmakesit-receptionist", "--config prd", "--access read", "--plain"):
        assert part in args
    assert (tmp_path / "ssh.stdin").read_text() == TOKEN
    assert TOKEN not in r.stdout + r.stderr + (tmp_path / "ssh.args").read_text()


@pytest.mark.parametrize("output,code", [("", 0), ("", 1), ("garbage-not-a-token", 0)])
def test_failed_or_empty_mint_never_reaches_the_pi(tmp_path, output, code):
    r = run(tmp_path, fakes(tmp_path, output, code))
    assert r.returncode != 0
    assert not (tmp_path / "ssh.args").exists()


def test_remote_side_writes_root_only_file_atomically():
    s = SCRIPT.read_text()
    assert "/etc/pi-receptionist/doppler.env" in s and "chmod 600" in s
    assert "mv -f" in s                                    # write a temp file, then rename
