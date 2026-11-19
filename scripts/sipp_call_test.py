"""T4: end-to-end phone-call test on the Pi with no provider and no minutes.

Piper speaks each caller turn; the audio becomes raw 8 kHz u-law for SIPp's
rtp_stream; SIPp dials the local Asterisk (endpoint "sipp", 127.0.0.1), which
Dial()s the receptionist over chan_websocket. Afterwards we read the live bookings
DB and the receptionist's per-phase log lines for the calls.

    PIPER_CLI=... PIPER_MODEL=... python scripts/sipp_call_test.py --calls 1 --concurrency 1
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]
SCENARIO = HERE.parent / "tests" / "sipp" / "receptionist_call.xml"
DB = "/var/lib/pi-receptionist/data/receptionist.sqlite"


def turn_audio(text: str, folder: Path) -> Path:
    import asr_sweep  # Piper -> the same 8 kHz u-law path as a phone call
    path = folder / (text.replace(" ", "_")[:40] + ".ulaw")
    path.write_bytes(asr_sweep.synth(text, folder))
    return path


def bookings_for(callers: list[str]) -> list[tuple]:
    q = ("import sqlite3,json,sys;d=sqlite3.connect(sys.argv[1]);"
         "print(json.dumps(d.execute('SELECT caller_id,status,starts FROM bookings WHERE caller_id IN (%s)'"
         "%','.join('?'*len(sys.argv[2:])),sys.argv[2:]).fetchall()))")
    out = subprocess.run(["sudo", "-u", "receptionist", "/opt/pi-receptionist/app/.venv/bin/python", "-c", q, DB, *callers],
                         capture_output=True, text=True, check=True).stdout
    return [tuple(r) for r in json.loads(out)]


def cleanup(callers: list[str]) -> None:
    """Cancel this run's test bookings so the live calendar is left as we found it."""
    q = ("import sqlite3,sys;d=sqlite3.connect(sys.argv[1]);"
         "d.execute('UPDATE bookings SET status=\'cancelled\' WHERE caller_id IN (%s)'"
         "%','.join('?'*len(sys.argv[2:])),sys.argv[2:]);d.commit()")
    subprocess.run(["sudo", "-u", "receptionist", "/opt/pi-receptionist/app/.venv/bin/python", "-c", q, DB, *callers],
                   check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--pause-ms", type=int, default=9000, help="wait after each turn for the reply")
    ap.add_argument("--day", default="december tenth")
    a = ap.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="sipp-"))
    # Each call asks for its own hour so concurrent test calls never compete for a slot.
    hours = ["nine am", "ten am", "eleven am", "noon", "one pm", "two pm", "three pm", "four pm"]
    yes = turn_audio("yes", work)
    base = int(time.time()) % 100000
    callers = [f"+1555{base:05d}{i:02d}" for i in range(a.calls)]
    lines = [f"{c};{turn_audio(f'book {a.day} at {hours[i % len(hours)]}', work)};{yes};x\n"
             for i, c in enumerate(callers)]
    inf = work / "calls.csv"
    inf.write_text("SEQUENTIAL\n" + "".join(lines))
    since = time.strftime("%Y-%m-%d %H:%M:%S")

    t0 = time.monotonic()
    sipp = subprocess.run(["sipp", "-sf", str(SCENARIO), "-inf", str(inf), "-s", "100", "127.0.0.1:5060",
                           "-i", "127.0.0.1", "-mi", "127.0.0.1", "-m", str(a.calls), "-l", str(a.concurrency),
                           "-r", str(a.concurrency), "-rp", "1000", "-d", str(a.pause_ms), "-trace_err", "-error_file", str(work / "err.log"),
                           "-timeout", str(int(a.calls * (2 * a.pause_ms / 1000 + 10))), "-nostdin"],
                          capture_output=True, text=True)
    took = time.monotonic() - t0

    logs = subprocess.run(["sudo", "journalctl", "-u", "pi-receptionist", "--since", since, "-o", "cat"],
                          capture_output=True, text=True).stdout
    phases = [l for l in logs.splitlines() if "phase=" in l]
    rows = bookings_for(callers)
    cleanup(callers)
    booked = {c for c, status, _ in rows if status == "active"}
    result = {"calls": a.calls, "concurrency": a.concurrency, "sipp_exit": sipp.returncode, "wall_s": round(took, 1),
              "booked": len(booked), "missing": sorted(set(callers) - booked), "phase_lines": phases[-8:],
              "sipp_errors": (work / "err.log").read_text()[-800:] if (work / "err.log").exists() else ""}
    print(json.dumps(result, indent=1))
    ok = sipp.returncode == 0 and len(booked) == a.calls
    print("SIPP_CALL_OK" if ok else "SIPP_CALL_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
