"""Copy the first-boot kit onto a freshly flashed Raspberry Pi OS boot partition.

Usage (laptop, after Raspberry Pi Imager finishes and the USB is re-plugged):
    python scripts/stage_bootfs.py E:\\                       # kit only
    python scripts/stage_bootfs.py E:\\ --autorun \\
        --tailscale-authkey-file %USERPROFILE%\\.secrets\\ts-pi.key   # unattended

Without --autorun only a `pi-receptionist/` folder is added and no boot file
is touched. --autorun adds ONE hook line to the Imager customisation script
(firstrun.sh or cloud-init user-data) so firstboot_install.sh runs as root on
first boot. The auth key is never printed or put in the manifest.
"""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ("scripts/pi_bootstrap.sh", "scripts/pi_preflight.sh", "scripts/firstboot_install.sh")
HOOK = ("for d in /boot/firmware /boot; do f=$d/pi-receptionist/firstboot_install.sh; "
        "[ -f $f ] && bash $f && break; done || true")
README = """Raspberry Pi 4 receptionist first-boot kit
Unattended (--autorun): nothing to do; check ~/pi-evidence/ or
    journalctl -u pi-receptionist-firstboot
Manual: as your normal user (not root):
    bash /boot/firmware/pi-receptionist/pi_bootstrap.sh
Verify files: cd /boot/firmware/pi-receptionist && sha256sum -c MANIFEST.sha256
"""


def hook_firstrun(path: Path) -> None:
    lines = path.read_text().split("\n")
    if any("firstboot_install.sh" in l for l in lines):
        return
    at = next((i for i, l in enumerate(lines) if l.startswith("rm -f /boot")), None)
    if at is None:
        at = next((i for i, l in enumerate(lines) if l.strip() == "exit 0"), len(lines))
    lines.insert(at, HOOK)
    path.write_bytes("\n".join(lines).encode())


def hook_cloud_init(path: Path) -> None:
    lines = path.read_text().split("\n")
    if any("firstboot_install.sh" in l for l in lines):
        return
    item = f"  - [bash, -c, '{HOOK}']"
    if "runcmd:" in lines:
        lines.insert(lines.index("runcmd:") + 1, item)
    else:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["runcmd:", item, ""]
    path.write_bytes("\n".join(lines).encode())


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bootfs", type=Path)
    ap.add_argument("--autorun", action="store_true")
    ap.add_argument("--tailscale-authkey-file", type=Path)
    a = ap.parse_args(argv[1:])
    bootfs = a.bootfs
    if not ((bootfs / "config.txt").is_file() and (bootfs / "cmdline.txt").is_file()):
        print(f"FAIL: {bootfs} has no config.txt + cmdline.txt; not a Pi boot partition",
              file=sys.stderr)
        return 3
    firstrun, userdata = bootfs / "firstrun.sh", bootfs / "user-data"
    if a.autorun and not (firstrun.is_file() or userdata.is_file()):
        print("FAIL: no firstrun.sh or user-data: apply Imager OS customisation "
              "(user, hostname, SSH key) and re-flash", file=sys.stderr)
        return 4
    dest = bootfs / "pi-receptionist"
    dest.mkdir(exist_ok=True)
    files = {Path(k).name: (ROOT / k).read_bytes().replace(b"\r\n", b"\n") for k in KIT}
    files["README.txt"] = README.encode()
    for name, data in files.items():
        (dest / name).write_bytes(data)
    manifest = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in files.items())
    (dest / "MANIFEST.sha256").write_bytes(manifest.encode())
    print(f"STAGED {len(files)} files to {dest}")
    print(manifest, end="")
    if a.tailscale_authkey_file:
        key = a.tailscale_authkey_file.read_text().strip()
        if not key.startswith("tskey-"):
            print("FAIL: auth key file does not look like a Tailscale key", file=sys.stderr)
            return 5
        (dest / "tailscale.authkey").write_text(key)
        print("STAGED tailscale.authkey (contents not shown; shredded on first boot)")
    if a.autorun:
        if firstrun.is_file():
            hook_firstrun(firstrun)
            print("AUTORUN hooked into firstrun.sh")
        else:
            hook_cloud_init(userdata)
            print("AUTORUN hooked into cloud-init user-data runcmd")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
