"""Measurements from a recorded test call (MixMonitor option D: interleaved stereo,
8 kHz signed 16-bit LE; left = caller -> Asterisk, right = receptionist -> caller).
Both directions share one clock, so gaps between them are real call timings."""
from __future__ import annotations

import math
import struct

FRAME = 160            # 20 ms at 8 kHz
VOICED = 500.0         # RMS above this is speech/agent audio
END_SILENCE = 15       # 300 ms of quiet ends a caller turn (same as the receptionist's VAD)
AGENT_QUIET = 10       # 200 ms of quiet means the agent stopped talking
STALL = 20             # >= 400 ms hole inside a reply = stall (TTS/transport underrun)
REPLY_GAP = 100        # >= 2 s of agent quiet separates replies


def _channels(raw: bytes):
    n = len(raw) // 4
    pairs = struct.unpack(f"<{2 * n}h", raw[:4 * n])
    return pairs[0::2], pairs[1::2]


def _rms(samples):
    out = []
    for i in range(0, len(samples) - FRAME + 1, FRAME):
        chunk = samples[i:i + FRAME]
        out.append(math.sqrt(sum(s * s for s in chunk) / FRAME))
    return out


def _runs(voiced):
    """[(start, end_exclusive)] of consecutive voiced frames."""
    runs, start = [], None
    for i, v in enumerate(voiced + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i)); start = None
    return runs


def analyse(raw: bytes) -> dict:
    left, right = _channels(raw)
    caller = [r > VOICED for r in _rms(left)]
    agent = [r > VOICED for r in _rms(right)]
    n = min(len(caller), len(agent))
    caller, agent = caller[:n], agent[:n]

    # Caller turns: voiced runs merged across gaps shorter than END_SILENCE.
    turns = []
    for s, e in _runs(caller):
        if turns and s - turns[-1][1] < END_SILENCE:
            turns[-1] = (turns[-1][0], e)
        else:
            turns.append((s, e))

    latencies, unanswered, barge = [], 0, []
    for s, e in turns:
        if any(agent[max(0, s - 1):s + 1]):
            # Caller started while the agent was talking: barge-in. Time until agent quiet.
            for j in range(s, n - AGENT_QUIET + 1):
                if not any(agent[j:j + AGENT_QUIET]):
                    barge.append(round((j - s) * 0.02, 3)); break
            continue
        nxt = next((j for j in range(e, n) if agent[j]), None)
        if nxt is None or nxt - e > 750:          # nothing within 15 s
            unanswered += 1
        else:
            latencies.append(round((nxt - e) * 0.02, 3))

    # Stalls: holes inside an agent reply (between voiced runs closer than REPLY_GAP).
    stalls = 0
    runs = _runs(agent)
    for (s1, e1), (s2, e2) in zip(runs, runs[1:]):
        gap = s2 - e1
        if STALL <= gap < REPLY_GAP and not any(caller[e1:s2]):
            stalls += 1

    clipped = sum(1 for x in right if abs(x) >= 32000)
    return {"seconds": round(n * 0.02, 2), "caller_turns": len(turns), "reply_latency_s": latencies,
            "unanswered_turns": unanswered, "barge_in_stop_s": barge, "stalls": stalls,
            "clipped_fraction": round(clipped / max(1, len(right)), 4)}
