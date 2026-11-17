"""Gate for scripts/stage_bootfs.py: copies the first-boot kit onto a freshly
flashed Raspberry Pi OS boot partition from a laptop. FIXTURE evidence only."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stage_bootfs.py"


def stage(target):
    return subprocess.run([sys.executable, str(SCRIPT), str(target)],
                          capture_output=True, text=True, timeout=30)


def fake_bootfs(tmp_path):
    b = tmp_path / "bootfs"
    b.mkdir()
    (b / "config.txt").write_text("arm_64bit=1\n")
    (b / "cmdline.txt").write_text("console=tty1\n")
    return b


def test_stages_kit_with_lf_endings(tmp_path):
    b = fake_bootfs(tmp_path)
    r = stage(b)
    assert r.returncode == 0, r.stderr
    kit = b / "pi-receptionist"
    for name in ("pi_bootstrap.sh", "pi_preflight.sh", "README.txt", "MANIFEST.sha256"):
        assert (kit / name).is_file(), name
    assert b"\r\n" not in (kit / "pi_bootstrap.sh").read_bytes()


def test_manifest_hashes_match(tmp_path):
    import hashlib
    b = fake_bootfs(tmp_path)
    assert stage(b).returncode == 0
    kit = b / "pi-receptionist"
    for line in (kit / "MANIFEST.sha256").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        assert hashlib.sha256((kit / name).read_bytes()).hexdigest() == digest


def test_refuses_non_bootfs_target(tmp_path):
    r = stage(tmp_path)
    assert r.returncode != 0
    assert not (tmp_path / "pi-receptionist").exists()


def test_does_not_touch_boot_files(tmp_path):
    b = fake_bootfs(tmp_path)
    before = {p.name: p.read_bytes() for p in b.iterdir()}
    assert stage(b).returncode == 0
    assert {p.name: p.read_bytes() for p in b.iterdir() if p.is_file()} == before


def test_never_stages_secrets(tmp_path):
    b = fake_bootfs(tmp_path)
    assert stage(b).returncode == 0
    names = {p.name for p in (b / "pi-receptionist").iterdir()}
    assert not any(n.startswith(".env") or n.endswith((".key", ".pem")) for n in names)
