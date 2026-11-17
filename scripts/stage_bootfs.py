"""Copy the first-boot kit onto a freshly flashed Raspberry Pi OS boot partition.

Usage (laptop, after Raspberry Pi Imager finishes and the USB is re-plugged):
    python scripts/stage_bootfs.py E:\\          # Windows drive letter of "bootfs"
    python scripts/stage_bootfs.py /media/$USER/bootfs

Only adds a `pi-receptionist/` folder; never edits config.txt, cmdline.txt or
any other boot file. On the Pi it appears at /boot/firmware/pi-receptionist/.
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ("scripts/pi_bootstrap.sh", "scripts/pi_preflight.sh")
README = """Raspberry Pi 4 receptionist first-boot kit
On the Pi, as your normal user (not root):
    bash /boot/firmware/pi-receptionist/pi_bootstrap.sh
Then follow docs/USB_BOOT_SETUP.md in the repo. Verify files first:
    cd /boot/firmware/pi-receptionist && sha256sum -c MANIFEST.sha256
"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    bootfs = Path(argv[1])
    if not ((bootfs / "config.txt").is_file() and (bootfs / "cmdline.txt").is_file()):
        print(f"FAIL: {bootfs} has no config.txt + cmdline.txt; not a Pi boot partition",
              file=sys.stderr)
        return 3
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
