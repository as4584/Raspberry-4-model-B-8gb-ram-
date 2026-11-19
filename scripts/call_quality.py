"""End-to-end quality suite over the real phone path (SIPp -> Asterisk -> receptionist),
free and local. Run on the Pi:

    PIPER_CLI=... PIPER_MODEL=... python scripts/call_quality.py --calls 10 --out ~/quality.json

Measures: reliability (calls completed, bookings saved, unanswered turns), reply latency
(end of caller speech -> first receptionist audio, from Asterisk's own aligned stereo
recording), audio quality (whisper re-transcribes the receptionist's side vs. what it was
meant to say; stalls; clipping), barge-in (time for the receptionist to go quiet when the
caller talks over it) and phone-path ASR accuracy (needs LOG_TRANSCRIPTS=1 on the service
for the run). Test bookings are cancelled afterwards; recordings are deleted.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]
import call_metrics  # noqa: E402
import sipp_call_test as S  # noqa: E402

MONITOR = "/var/spool/asterisk/monitor"
CALL_XML = HERE.parent / "tests" / "sipp" / "receptionist_call.xml"
BARGE_XML = HERE.parent / "tests" / "sipp" / "receptionist_bargein.xml"
HOURS = ["nine am", "ten am", "eleven am", "noon", "one pm", "two pm", "three pm", "four pm"]
DAYS = ["december tenth", "december eleventh", "december fourteenth", "december fifteenth",
        "december sixteenth", "december seventeenth"]


def sipp(xml: Path, rows: list[str], concurrency: int, pause_ms: int, work: Path) -> int:
    inf = work / f"inf-{time.monotonic_ns()}.csv"
    inf.write_text("SEQUENTIAL\n" + "".join(rows))
    r = subprocess.run(["sipp", "-sf", str(xml), "-inf", str(inf), "-s", "rec10", "127.0.0.1:5070",
                        "-i", "127.0.0.1", "-mi", "127.0.0.1", "-m", str(len(rows)), "-l", str(concurrency),
                        "-r", str(concurrency), "-rp", "1000", "-d", str(pause_ms), "-nostdin",
                        "-timeout", str(len(rows) * 40)], capture_output=True, text=True)
    return r.returncode


def recording(caller: str) -> bytes:
    path = f"{MONITOR}/sipp-{caller[1:]}.raw"
    raw = subprocess.run(["sudo", "cat", path], capture_output=True).stdout
    subprocess.run(["sudo", "rm", "-f", path])
    return raw


async def transcribe_agent(raw: bytes) -> str:
    """Re-transcribe the receptionist's side of the call (right channel) with whisper."""
    from receptionist.audio import WhisperDaemonASR, mulaw_from_pcm16
    import struct
    n = len(raw) // 4
    right = struct.unpack(f"<{2 * n}h", raw[:4 * n])[1::2]
    pcm = struct.pack(f"<{len(right)}h", *right)
    asr = WhisperDaemonASR(["/home/prototype/opt/whisper.cpp-v1.9.5/build/bin/whisper-daemon",
                            "/home/prototype/models/whisper/ggml-base.en-q5_1.bin", "4"], timeout=60)
    try:
        # Whole call at once: transcribe in 20 s chunks to stay inside the decoder window.
        mu = mulaw_from_pcm16(pcm)
        parts = [await asr.transcribe(mu[i:i + 160000]) for i in range(0, len(mu), 160000)]
        return " ".join(p for p in parts if p)
    finally:
        await asr.aclose()


def journal_since(since: str) -> list[str]:
    out = subprocess.run(["sudo", "journalctl", "-u", "pi-receptionist", "--since", since, "-o", "cat"],
                         capture_output=True, text=True).stdout
    return out.splitlines()


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(q * len(v)))], 3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=10)
    ap.add_argument("--barge", type=int, default=5)
    ap.add_argument("--concurrent", type=int, default=3)
    ap.add_argument("--pause-ms", type=int, default=9000)
    ap.add_argument("--out", default=str(Path.home() / "call-quality.json"))
    a = ap.parse_args(argv)
    from receptionist.asr_eval import wer
    from receptionist.dialogue import GREETING

    work = Path(tempfile.mkdtemp(prefix="quality-"))
    yes = S.turn_audio("yes", work)
    base = int(time.time()) % 100000
    report: dict = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "throttled": subprocess.run(
        ["vcgencmd", "get_throttled"], capture_output=True, text=True).stdout.strip()}
    all_callers: list[str] = []

    def batch(name, n, xml, concurrency):
        callers, rows, phrases = [], [], []
        for i in range(n):
            c = f"+1556{base:05d}{len(all_callers) + i:02d}"
            phrase = f"book {DAYS[(len(all_callers) + i) // len(HOURS) % len(DAYS)]} at {HOURS[(len(all_callers) + i) % len(HOURS)]}"
            callers.append(c); phrases.append(phrase)
            rows.append(f"{c};{S.turn_audio(phrase, work)};{yes};x\n")
        all_callers.extend(callers)
        since = time.strftime("%Y-%m-%d %H:%M:%S")
        t0 = time.monotonic()
        code = sipp(xml, rows, concurrency, a.pause_ms, work)
        wall = time.monotonic() - t0
        booked = {c for c, status, _ in S.bookings_for(callers) if status == "active"}
        per_call = []
        for c, phrase in zip(callers, phrases):
            raw = recording(c)
            m = call_metrics.analyse(raw) if raw else {"missing_recording": True}
            m["caller"], m["phrase"], m["booked"] = c, phrase, c in booked
            m["_raw"] = raw
            per_call.append(m)
        heard = [l.split("heard=", 1)[1] for l in journal_since(since) if "heard=" in l]
        return {"name": name, "sipp_exit": code, "wall_s": round(wall, 1), "calls": per_call,
                "booked": len(booked), "heard": heard}

    seq = batch("sequential", a.calls, CALL_XML, 1)
    barge = batch("barge_in", a.barge, BARGE_XML, 1)
    conc = batch("concurrent", a.concurrent, CALL_XML, a.concurrent)

    # Audio quality: whisper re-transcribes the receptionist's side of a few calls.
    intelligibility = []
    for m in seq["calls"][:3]:
        if m.get("_raw"):
            text = asyncio.run(transcribe_agent(m["_raw"]))
            intelligibility.append({"caller": m["caller"], "heard_agent": text,
                                    "greeting_wer": round(wer(GREETING, text.split("?")[0] + "?"), 3)})

    # Phone-path ASR accuracy: what the receptionist heard vs. what the caller said.
    asr_pairs = []
    for b in (seq,):
        heard = [h.strip("'\"") for h in b["heard"]]
        said = [x for m in b["calls"] for x in (m["phrase"], "yes")]
        asr_pairs = list(zip(said, heard))
    asr_wer = [wer(s, h) for s, h in asr_pairs]

    latencies = [x for b in (seq, conc) for m in b["calls"] for x in m.get("reply_latency_s", [])]
    seq_lat = [x for m in seq["calls"] for x in m.get("reply_latency_s", [])]
    stops = [x for m in barge["calls"] for x in m.get("barge_in_stop_s", [])]
    for b in (seq, barge, conc):
        for m in b["calls"]:
            m.pop("_raw", None)
    report.update({
        "reliability": {b["name"]: {"calls": len(b["calls"]), "sipp_exit": b["sipp_exit"], "booked": b["booked"],
                                    "unanswered_turns": sum(m.get("unanswered_turns", 0) for m in b["calls"]),
                                    "missing_recordings": sum(1 for m in b["calls"] if m.get("missing_recording"))}
                        for b in (seq, barge, conc)},
        "latency_s": {"sequential_p50": pct(seq_lat, .5), "sequential_p95": pct(seq_lat, .95),
                      "sequential_max": max(seq_lat) if seq_lat else None,
                      "concurrent_p50": pct([x for m in conc["calls"] for x in m.get("reply_latency_s", [])], .5),
                      "concurrent_max": max([x for m in conc["calls"] for x in m.get("reply_latency_s", [])] or [None],
                                            key=lambda v: v or 0),
                      "n": len(latencies)},
        "barge_in_stop_s": {"p50": pct(stops, .5), "max": max(stops) if stops else None, "n": len(stops)},
        "audio": {"stalls": sum(m.get("stalls", 0) for b in (seq, conc) for m in b["calls"]),
                  "max_clipped_fraction": max((m.get("clipped_fraction", 0) for b in (seq, conc) for m in b["calls"]),
                                              default=0),
                  "intelligibility": intelligibility},
        "phone_path_asr": {"n": len(asr_pairs), "exact": sum(1 for w in asr_wer if w == 0),
                           "mean_wer": round(statistics.mean(asr_wer), 3) if asr_wer else None,
                           "misses": [(s, h) for (s, h), w in zip(asr_pairs, asr_wer) if w > 0][:8]},
        "calls": {b["name"]: b["calls"] for b in (seq, barge, conc)},
    })
    S.cleanup(all_callers)
    Path(a.out).write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "calls"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
