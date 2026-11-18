"""S8: off-device backup. scripts/pi_backup_export.sh is the ONLY thing the
restricted 'backupreader' SSH key may run: it streams the newest snapshot +
its sha256 sidecar as a tar on stdout, and nothing else."""
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pi_backup_export.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or shutil.which("tar") is None, reason="bash+tar required")


def run(tmp_path, **env):
    e = {**os.environ, "BACKUP_DIR": str(tmp_path / "b"), **env}
    return subprocess.run([BASH, str(SCRIPT)], env=e, capture_output=True, timeout=30)


def make(tmp_path, *stamps):
    d = tmp_path / "b"
    d.mkdir(exist_ok=True)
    for s in stamps:
        (d / f"bookings-{s}.sqlite").write_bytes(f"db {s}".encode())
        (d / f"bookings-{s}.sha256").write_text(f"x  bookings-{s}.sqlite\n")
    return d


def test_streams_only_newest_snapshot_and_sidecar(tmp_path):
    make(tmp_path, "20261117T033000Z", "20261118T033000Z")
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    names = tarfile.open(fileobj=io.BytesIO(r.stdout)).getnames()
    assert sorted(names) == ["bookings-20261118T033000Z.sha256", "bookings-20261118T033000Z.sqlite"]


def test_no_snapshot_is_an_error_with_empty_stdout(tmp_path):
    make(tmp_path)
    r = run(tmp_path)
    assert r.returncode == 70 and r.stdout == b"" and b"FAIL" in r.stderr


def test_ignores_any_requested_command(tmp_path):
    """sshd passes the client's command in SSH_ORIGINAL_COMMAND; it must not matter."""
    make(tmp_path, "20261118T033000Z")
    r = run(tmp_path, SSH_ORIGINAL_COMMAND="cat /etc/shadow; rm -rf /")
    assert r.returncode == 0
    assert sorted(tarfile.open(fileobj=io.BytesIO(r.stdout)).getnames())[0].startswith("bookings-")


def test_snapshot_without_sidecar_is_refused(tmp_path):
    d = make(tmp_path, "20261118T033000Z")
    (d / "bookings-20261118T033000Z.sha256").unlink()
    r = run(tmp_path)
    assert r.returncode == 71 and r.stdout == b""
