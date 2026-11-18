"""Bounded soak test (S5 T10): N simultaneous WebSocket callers make bookings
against a REAL uvicorn server process for M minutes, then the invariants are
checked. Server and callers are separate processes, so the server's RSS
samples are its own (Linux /proc only).

    python scripts/soak_calls.py --minutes 10 --callers 4 --out soak.jsonl

Scripted mock ASR + silent TTS: this measures transport, threading, DB and
memory stability, NOT speech or model latency. Uses a throwaway DB."""
import argparse
import base64
import itertools
import json
import multiprocessing as mp
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date, timedelta

LOUD, QUIET = bytes([0] * 160), bytes([255] * 160)


def slots():
    """Unique weekday 30-minute slots inside default business hours."""
    d = date(2027, 1, 4)
    while True:
        if d.weekday() < 5:
            for h in range(9, 17):
                for m in (0, 30):
                    yield f"{d.isoformat()} {h:02d}:{m:02d}"
        d += timedelta(days=1)


def _serve(db, port, ready):
    import uvicorn
    from receptionist.audio import MockASR, MockTTS
    from receptionist.server import Settings, create_app
    gen = slots()
    lock = threading.Lock()

    def asr_factory():
        with lock:
            slot = next(gen)
        return MockASR([f"book {slot}", "yes"])

    app = create_app(Settings(db_path=db, dev_allow_unsigned=True, wss_url="wss://localhost/twilio/media"),
                     asr_factory=asr_factory, tts=MockTTS())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def signal_ready():
        while not server.started:
            time.sleep(0.05)
        ready.set()

    threading.Thread(target=signal_ready, daemon=True).start()
    server.run()


def _rss_kb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def _call(port, n):
    from websockets.sync.client import connect
    sid = f"MS{n}"
    with connect(f"ws://127.0.0.1:{port}/twilio/media", open_timeout=10) as ws:
        ws.send(json.dumps({"event": "start", "streamSid": sid,
                            "start": {"callSid": f"CS{n}", "customParameters": {"callerId": f"+1555{n:07d}"}}}))
        for _ in range(2):                                   # "book <slot>", then "yes"
            for chunk in [LOUD] * 5 + [QUIET] * 16:
                ws.send(json.dumps({"event": "media", "streamSid": sid,
                                    "media": {"payload": base64.b64encode(chunk).decode()}}))
            events = [json.loads(ws.recv(timeout=30))["event"] for _ in range(4)]
            if events != ["clear", "media", "media", "mark"]:
                raise RuntimeError(f"unexpected events {events}")
        ws.send(json.dumps({"event": "stop", "streamSid": sid}))


def check_db(db):
    c = sqlite3.connect(db)
    rows = c.execute("SELECT resource, starts, ends FROM bookings WHERE status='active' ORDER BY resource, starts").fetchall()
    overlaps = sum(1 for a, b in zip(rows, rows[1:]) if a[0] == b[0] and b[1] < a[2])
    integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
    c.close()
    return len(rows), overlaps, integrity


def soak(minutes, callers, out=None):
    tmp = tempfile.mkdtemp(prefix="soak-")
    db = os.path.join(tmp, "soak.sqlite")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    server = ctx.Process(target=_serve, args=(db, port, ready), daemon=True)
    server.start()
    if not ready.wait(60):
        server.kill()
        raise RuntimeError("server did not start")
    counter = itertools.count()
    stats = {"ok": 0, "fail": 0, "errors": []}
    lock = threading.Lock()
    deadline = time.monotonic() + minutes * 60

    def worker():
        while time.monotonic() < deadline:
            n = next(counter)
            try:
                _call(port, n)
                with lock:
                    stats["ok"] += 1
            except Exception as exc:                      # recorded, never hidden
                with lock:
                    stats["fail"] += 1
                    if len(stats["errors"]) < 5:
                        stats["errors"].append(repr(exc)[:200])

    threads = [threading.Thread(target=worker) for _ in range(callers)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    samples = []
    while any(t.is_alive() for t in threads):
        samples.append({"t_s": round(time.monotonic() - t0, 1), "server_rss_kb": _rss_kb(server.pid), "ok": stats["ok"]})
        for t in threads:
            t.join(timeout=15 / max(len(threads), 1))
    samples.append({"t_s": round(time.monotonic() - t0, 1), "server_rss_kb": _rss_kb(server.pid), "ok": stats["ok"]})
    server.kill()
    server.join(10)
    bookings, overlaps, integrity = check_db(db)
    rss = [x["server_rss_kb"] for x in samples if x["server_rss_kb"]]
    summary = {"minutes": minutes, "callers": callers, "calls_ok": stats["ok"], "calls_failed": stats["fail"],
               "errors": stats["errors"], "bookings": bookings, "overlaps": overlaps, "integrity": integrity,
               "rss_first_kb": rss[0] if rss else None, "rss_last_kb": rss[-1] if rss else None,
               "rss_max_kb": max(rss) if rss else None, "elapsed_s": round(time.monotonic() - t0, 1)}
    if out:
        with open(out, "w") as f:
            for x in samples:
                f.write(json.dumps(x) + "\n")
            f.write(json.dumps({"summary": summary}) + "\n")
    return summary


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--callers", type=int, default=4)
    ap.add_argument("--out")
    a = ap.parse_args(argv[1:])
    s = soak(a.minutes, a.callers, a.out)
    print(json.dumps(s, indent=1))
    ok = s["calls_failed"] == 0 and s["overlaps"] == 0 and s["integrity"] == "ok" and s["bookings"] == s["calls_ok"]
    print("SOAK_OK" if ok else "SOAK_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
