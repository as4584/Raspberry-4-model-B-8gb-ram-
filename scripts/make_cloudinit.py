"""Write cloud-init seed files (user-data, network-config, meta-data) onto a
freshly flashed Raspberry Pi OS (Trixie or later) boot partition, the same
job as Imager's "OS customisation". Secrets come from local files outside git
and are never printed. Run stage_bootfs.py --autorun afterwards.

    python scripts/make_cloudinit.py E:\\ --wifi-file %USERPROFILE%\\.secrets\\pi-wifi.txt \\
        --login-file %USERPROFILE%\\.secrets\\pi-login.txt --ssh-pubkey %USERPROFILE%\\.ssh\\id_ed25519.pub

wifi file: SSID=..., PSK=..., COUNTRY=US   login file: user=..., password=...
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOSTNAME = "lex-receptionist-pi"
GROUPS = "users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo"


def kv(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def sha512_crypt(password: str) -> str:
    openssl = shutil.which("openssl")
    if not openssl:
        raise SystemExit("FAIL: openssl needed to hash the password")
    r = subprocess.run([openssl, "passwd", "-6", "-stdin"], input=password,
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def fail(msg: str) -> int:
    print(f"FAIL: {msg}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bootfs", type=Path)
    ap.add_argument("--wifi-file", type=Path)
    ap.add_argument("--login-file", type=Path, required=True)
    ap.add_argument("--ssh-pubkey", type=Path, required=True)
    ap.add_argument("--timezone", default="America/New_York")
    a = ap.parse_args(argv[1:])
    b = a.bootfs
    if not ((b / "config.txt").is_file() and (b / "cmdline.txt").is_file()):
        return fail(f"{b} is not a Raspberry Pi boot partition")
    login = kv(a.login_file)
    user, password = login.get("user", ""), login.get("password", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user) or user in ("root", "pi"):
        return fail("username must be a normal lowercase name, not root or pi")
    if len(password) < 12:
        return fail("password must be at least 12 characters")
    pub = a.ssh_pubkey.read_text().strip()
    if "PRIVATE KEY" in pub:
        return fail("that is a private key; pass the .pub file")
    if not re.match(r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+) \S+", pub):
        return fail("ssh public key not recognised")

    ud = [
        "#cloud-config",
        f"hostname: {HOSTNAME}",
        "manage_etc_hosts: true",
        f"timezone: {json.dumps(a.timezone)}",
        "ssh_pwauth: false",
        "users:",
        f"  - name: {json.dumps(user)}",
        f"    groups: {GROUPS}",
        "    shell: /bin/bash",
        "    lock_passwd: false",
        f"    passwd: {json.dumps(sha512_crypt(password))}",
        "    sudo: ALL=(ALL) NOPASSWD:ALL",
        "    ssh_authorized_keys:",
        f"      - {json.dumps(pub)}",
        "runcmd:",
        "  - [rfkill, unblock, wifi]",
        "  - [systemctl, enable, --now, ssh]",
        "",
    ]
    nc = ["network:", "  version: 2"]
    if a.wifi_file:
        w = kv(a.wifi_file)
        if not w.get("SSID") or len(w.get("PSK", "")) < 8:
            return fail("wifi file needs SSID= and an 8+ character PSK=")
        nc += [
            "  wifis:",
            "    renderer: NetworkManager",
            "    wlan0:",
            "      dhcp4: true",
            "      optional: true",
            f"      regulatory-domain: {json.dumps(w.get('COUNTRY', 'US'))}",
            "      access-points:",
            f"        {json.dumps(w['SSID'])}:",
            f"          password: {json.dumps(w['PSK'])}",
        ]
    nc += [""]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    files = {"user-data": ud, "network-config": nc,
             # dsmode local is the Pi OS image default: apply user-data before network.
             "meta-data": ["dsmode: local", f"instance-id: {HOSTNAME}-{stamp}", ""]}
    for name, lines in files.items():
        (b / name).write_bytes("\n".join(lines).encode())
    print(f"SEEDED user-data, network-config, meta-data on {b} "
          f"(user={user}, hostname={HOSTNAME}, wifi={'yes' if a.wifi_file else 'no'}; secrets not shown)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
