"""Natural multi-turn conversations over the real phone path, free and local:
Piper speaks each caller turn -> SIPp -> Asterisk -> receptionist. Results come from
review mode (heard / action / reply / latency per turn) and the bookings DB.

Needs the test caller in REVIEW_CALLERS on the Pi (see --caller). Run on the Pi:
    PIPER_CLI=... PIPER_MODEL=... python scripts/conversation_suite.py [--only NAME]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]
import sipp_call_test as S  # noqa: E402

SUITE = HERE.parent / "tests" / "conversations" / "natural.json"
REVIEW = "/var/lib/pi-receptionist/data/review"
GREETING_MS = 3400          # caller listens to the greeting (silence RTP) before turn 1
TURN_MS = 11000             # time allowed per turn: speech + reply

SCENARIO_HEAD = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<scenario name="conversation">
  <send retrans="500"><![CDATA[
      INVITE sip:[service]@[remote_ip]:[remote_port] SIP/2.0
      Via: SIP/2.0/[transport] [local_ip]:[local_port];branch=[branch]
      From: <sip:{caller}@[local_ip]:[local_port]>;tag=[pid]SIPpTag00[call_number]
      To: <sip:[service]@[remote_ip]:[remote_port]>
      Call-ID: [call_id]
      CSeq: 1 INVITE
      Contact: sip:{caller}@[local_ip]:[local_port]
      Max-Forwards: 70
      Content-Type: application/sdp
      Content-Length: [len]

      v=0
      o=user1 53655765 2353687637 IN IP[local_ip_type] [local_ip]
      s=-
      c=IN IP[media_ip_type] [media_ip]
      t=0 0
      m=audio [media_port] RTP/AVP 0
      a=rtpmap:0 PCMU/8000
  ]]></send>
  <recv response="100" optional="true"/>
  <recv response="180" optional="true"/>
  <recv response="183" optional="true"/>
  <recv response="200" rtd="true"/>
  <send><![CDATA[
      ACK sip:[service]@[remote_ip]:[remote_port] SIP/2.0
      Via: SIP/2.0/[transport] [local_ip]:[local_port];branch=[branch]
      From: <sip:{caller}@[local_ip]:[local_port]>;tag=[pid]SIPpTag00[call_number]
      To: <sip:[service]@[remote_ip]:[remote_port]>[peer_tag_param]
      Call-ID: [call_id]
      CSeq: 1 ACK
      Contact: sip:{caller}@[local_ip]:[local_port]
      Max-Forwards: 70
      Content-Length: 0
  ]]></send>
  <pause milliseconds="200"/>
"""
SCENARIO_TURN = """  <nop><action><exec rtp_stream="{file},1,0"/></action></nop>
  <pause milliseconds="{ms}"/>
"""
SCENARIO_TAIL = """  <send retrans="500"><![CDATA[
      BYE sip:[service]@[remote_ip]:[remote_port] SIP/2.0
      Via: SIP/2.0/[transport] [local_ip]:[local_port];branch=[branch]
      From: <sip:{caller}@[local_ip]:[local_port]>;tag=[pid]SIPpTag00[call_number]
      To: <sip:[service]@[remote_ip]:[remote_port]>[peer_tag_param]
      Call-ID: [call_id]
      CSeq: 2 BYE
      Contact: sip:{caller}@[local_ip]:[local_port]
      Max-Forwards: 70
      Content-Length: 0
  ]]></send>
  <recv response="200" crlf="true"/>
</scenario>
"""


def active_bookings(caller: str) -> int:
    return sum(1 for _, status, _ in S.bookings_for([caller]) if status == "active")


def newest_review(after: float) -> list[dict]:
    out = subprocess.run(["sudo", "sh", "-c", f"ls -t {REVIEW}/*.jsonl 2>/dev/null | head -1"],
                         capture_output=True, text=True).stdout.strip()
    if not out:
        return []
    rows = [json.loads(l) for l in subprocess.run(["sudo", "cat", out], capture_output=True, text=True).stdout.splitlines()]
    return rows if rows and rows[0]["t"] >= after - 1 else []


def run_one(sc: dict, caller: str, work: Path) -> dict:
    files = [S.turn_audio(t, work, lead_ms=GREETING_MS if i == 0 else 0) for i, t in enumerate(sc["turns"])]
    xml = SCENARIO_HEAD.format(caller=caller)
    for i, f in enumerate(files):
        xml += SCENARIO_TURN.format(file=f, ms=TURN_MS + (GREETING_MS if i == 0 else 0))
    xml += SCENARIO_TAIL.format(caller=caller)
    path = work / f"{sc['name']}.xml"
    path.write_text(xml)
    before = active_bookings(caller)
    t0 = time.time()
    r = subprocess.run(["sipp", "-sf", str(path), "-s", "100", "127.0.0.1:5070", "-i", "127.0.0.1", "-mi", "127.0.0.1",
                        "-m", "1", "-nostdin", "-timeout", str(len(files) * 20 + 30)], capture_output=True, text=True)
    time.sleep(2)
    rows = newest_review(t0)
    turns = [x for x in rows if x.get("type") == "turn"]
    lat = [x["end_of_speech_to_audio_s"] for x in rows if x.get("type") == "latency"]
    booked = active_bookings(caller) > before
    S.cleanup([caller])
    exp, problems = sc.get("expect", {}), []
    if "booked" in exp and booked != exp["booked"]:
        problems.append(f"booked={booked}, expected {exp['booked']}")
    if exp.get("action") and exp["action"] not in [t["action"] for t in turns]:
        problems.append(f"no '{exp['action']}' action")
    if exp.get("reply_mentions") and not any(any(m in t["reply"] for m in exp["reply_mentions"]) for t in turns):
        problems.append(f"reply never mentions {exp['reply_mentions']}")
    if not turns:
        problems.append("no turns captured (is the test caller in REVIEW_CALLERS?)")
    return {"name": sc["name"], "pass": not problems and r.returncode == 0, "problems": problems,
            "sipp_exit": r.returncode, "latency_s": lat,
            "turns": [{"said": s, "heard": t["heard"], "action": t["action"], "reply": t["reply"]}
                      for s, t in zip(sc["turns"], turns)]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--caller", default="+15550001000")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=str(Path.home() / "conversations.json"))
    a = ap.parse_args(argv)
    suite = [s for s in json.loads(SUITE.read_text()) if not a.only or s["name"] == a.only]
    work = Path(tempfile.mkdtemp(prefix="conv-"))
    results = [run_one(s, a.caller, work) for s in suite]
    Path(a.out).write_text(json.dumps(results, indent=1))
    for r in results:
        print(("PASS " if r["pass"] else "FAIL ") + r["name"] + ("" if r["pass"] else "  <- " + "; ".join(r["problems"])))
        for t in r["turns"]:
            print(f"    caller: {t['said']!r}\n      heard: {t['heard']!r} -> {t['action']}\n      reply: {t['reply']!r}")
        if r["latency_s"]:
            print(f"    reply latency: {r['latency_s']}")
    lat = sorted(x for r in results for x in r["latency_s"])
    print(f"\n{sum(r['pass'] for r in results)}/{len(results)} conversations passed"
          + (f"; reply latency p50 {lat[len(lat) // 2]:.2f}s max {lat[-1]:.2f}s" if lat else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
