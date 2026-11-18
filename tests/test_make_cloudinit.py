"""Gate for scripts/make_cloudinit.py: writes cloud-init seed files onto a
flashed Raspberry Pi OS (Trixie+) bootfs. Secrets are read from local files
and must never be echoed. FIXTURE evidence only."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_cloudinit.py"
PSK = "fixture-psk-123"
PW = "fixture-password-xyz"
PUB = "ssh-ed25519 AAAAC3NzaFIXTURE laptop1"


def setup(tmp_path):
    b = tmp_path / "bootfs"
    b.mkdir()
    (b / "config.txt").write_text("x")
    (b / "cmdline.txt").write_text("x")
    wifi = tmp_path / "wifi.txt"
    wifi.write_text(f"SSID=Home Net\nPSK={PSK}\nCOUNTRY=US\n")
    login = tmp_path / "login.txt"
    login.write_text(f"user=prototype\npassword={PW}\n")
    pub = tmp_path / "id.pub"
    pub.write_text(PUB + "\n")
    return b, wifi, login, pub


def run(b, wifi, login, pub, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), str(b), "--wifi-file", str(wifi),
                           "--login-file", str(login), "--ssh-pubkey", str(pub), *extra],
                          capture_output=True, text=True, timeout=60)


def test_writes_seed_files_without_leaking_secrets(tmp_path):
    b, wifi, login, pub = setup(tmp_path)
    r = run(b, wifi, login, pub)
    assert r.returncode == 0, r.stderr
    ud = (b / "user-data").read_text()
    nc = (b / "network-config").read_text()
    assert ud.startswith("#cloud-config\n")
    assert "hostname: lex-receptionist-pi" in ud
    assert '- name: "prototype"' in ud
    assert PUB in ud
    assert "ssh_pwauth: false" in ud
    assert PW not in ud and "passwd: \"$6$" in ud
    assert json.dumps("Home Net") in nc and PSK in nc
    assert PSK not in ud
    md = (b / "meta-data").read_text()
    assert "dsmode: local" in md  # Pi OS image default; apply user-data before network
    assert "instance-id: lex-receptionist-pi-" in md
    assert PSK not in r.stdout + r.stderr and PW not in r.stdout + r.stderr


def test_seed_is_lf_only(tmp_path):
    b, wifi, login, pub = setup(tmp_path)
    assert run(b, wifi, login, pub).returncode == 0
    for name in ("user-data", "network-config", "meta-data"):
        assert b"\r\n" not in (b / name).read_bytes()


def test_rejects_root_or_pi_username(tmp_path):
    b, wifi, login, pub = setup(tmp_path)
    for bad in ("root", "pi"):
        login.write_text(f"user={bad}\npassword={PW}\n")
        r = run(b, wifi, login, pub)
        assert r.returncode != 0 and "FAIL:" in r.stderr
        assert not (b / "user-data").exists()


def test_rejects_non_bootfs(tmp_path):
    _, wifi, login, pub = setup(tmp_path)
    r = run(tmp_path, wifi, login, pub)
    assert r.returncode != 0 and not (tmp_path / "user-data").exists()


def test_rejects_private_key_as_pubkey(tmp_path):
    b, wifi, login, pub = setup(tmp_path)
    pub.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
    r = run(b, wifi, login, pub)
    assert r.returncode != 0 and "private" in r.stderr.lower()
