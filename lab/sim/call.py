"""Talk to the two-way lab on the Pi, one turn at a time (see chat_server.py).

    python lab/sim/call.py start SESSION
    python lab/sim/call.py say SESSION "what the caller says"     # heard through phone-quality ASR
    python lab/sim/call.py say SESSION "..." --exact             # skip the ASR (brain only)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

PI = "prototype@100.92.161.12"


def post(path: str, body: dict) -> dict:
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", PI,
                        f"curl -s -m 120 -XPOST localhost:8099{path} --data-binary @-"],
                       input=json.dumps(body), capture_output=True, text=True, timeout=180,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"lab unreachable: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start", "say"])
    ap.add_argument("session")
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--exact", action="store_true")
    a = ap.parse_args(argv)
    out = post("/start", {"session": a.session}) if a.cmd == "start" else \
        post("/turn", {"session": a.session, "text": a.text, "ears": not a.exact})
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
