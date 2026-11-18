"""Readiness probes for /readyz (S8). Each probe is a module-level function so
tests can replace it; off-Pi (no vcgencmd) power and temperature report unknown
and do not block readiness."""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess

MIN_FREE_MB = 500
MAX_TEMP_C = 80.0
# sysfs fallbacks: readable by the unprivileged service user, unlike vcgencmd
# (/dev/vcio) under PrivateDevices=yes. rpi_volt only reports "now".
HWMON_ROOT = "/sys/class/hwmon"
THERMAL_ZONE = "/sys/class/thermal/thermal_zone0/temp"


def parse_throttled(text: str) -> str | None:
    m = re.search(r"throttled=(0x[0-9a-fA-F]+)", text or "")
    return m.group(1) if m else None


def parse_temp(text: str) -> float | None:
    m = re.search(r"temp=([0-9.]+)", text or "")
    return float(m.group(1)) if m else None


def _vcgencmd(*args: str) -> str:
    exe = shutil.which("vcgencmd")
    if not exe:
        return ""
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def read_throttled() -> str | None:
    return parse_throttled(_vcgencmd("get_throttled"))


def read_temp_c() -> float | None:
    return parse_temp(_vcgencmd("measure_temp"))


def read_rpi_volt_alarm() -> int | None:
    import glob
    import os
    for h in sorted(glob.glob(os.path.join(HWMON_ROOT, "hwmon*"))):
        try:
            with open(os.path.join(h, "name")) as f:
                if f.read().strip() != "rpi_volt":
                    continue
            with open(os.path.join(h, "in0_lcrit_alarm")) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            continue
    return None


def read_thermal_zone_c() -> float | None:
    try:
        with open(THERMAL_ZONE) as f:
            return int(f.read().strip()) / 1000
    except (OSError, ValueError):
        return None


def free_mb(path: str) -> float:
    return shutil.disk_usage(path).free / 1e6


def check_database(db_path: str) -> dict:
    db = sqlite3.connect(db_path, timeout=0.5, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=500")
        db.execute("BEGIN IMMEDIATE")      # can we take the write lock right now?
        db.execute("ROLLBACK")
        return {"name": "database", "ok": True, "detail": "write lock available"}
    except sqlite3.Error as exc:
        return {"name": "database", "ok": False, "detail": f"{exc}"}
    finally:
        db.close()


def check_disk(path: str) -> dict:
    mb = free_mb(path)
    return {"name": "disk", "ok": mb >= MIN_FREE_MB, "detail": f"{mb:.0f} MB free (min {MIN_FREE_MB})"}


def check_power() -> dict:
    t = read_throttled()
    if t is None:
        alarm = read_rpi_volt_alarm()
        if alarm is None:
            return {"name": "power", "ok": True, "detail": "unknown (no vcgencmd, no rpi_volt)"}
        if alarm:
            return {"name": "power", "ok": False, "detail": "under-voltage now (rpi_volt alarm)"}
        return {"name": "power", "ok": True, "detail": "rpi_volt alarm clear (history unavailable)"}
    v = int(t, 16)
    if v & 0x5:
        return {"name": "power", "ok": False, "detail": f"under-voltage or throttled now ({t})"}
    note = "; under-voltage/throttling since boot" if v & 0x50000 else ""
    return {"name": "power", "ok": True, "detail": f"{t}{note}"}


def check_temperature() -> dict:
    c = read_temp_c()
    if c is None:
        c = read_thermal_zone_c()
    if c is None:
        return {"name": "temperature", "ok": True, "detail": "unknown (no vcgencmd)"}
    return {"name": "temperature", "ok": c < MAX_TEMP_C, "detail": f"{c:.1f} C (max {MAX_TEMP_C:.0f})"}


def readiness(db_path: str, data_dir: str) -> dict:
    checks = [check_database(db_path), check_disk(data_dir), check_power(), check_temperature()]
    return {"ready": all(c["ok"] for c in checks), "checks": checks}
