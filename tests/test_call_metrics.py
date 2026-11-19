"""Measurement maths for recorded test calls (MixMonitor D: interleaved stereo 8 kHz
s16, left = caller -> Asterisk, right = receptionist -> caller). Synthetic audio here;
the Pi runs it on real SIPp calls (scripts/call_quality.py)."""
import math
import struct

from scripts.call_metrics import analyse

FRAME = 160                              # 20 ms at 8 kHz


def tone(n_frames, amp=8000):
    return [int(amp * math.sin(i / 3)) for i in range(n_frames * FRAME)]


def quiet(n_frames):
    return [0] * (n_frames * FRAME)


def stereo(left, right):
    n = max(len(left), len(right))
    left += [0] * (n - len(left)); right += [0] * (n - len(right))
    return b"".join(struct.pack("<hh", a, b) for a, b in zip(left, right))


def test_reply_latency_is_end_of_caller_speech_to_first_agent_audio():
    # caller talks 1 s, stops; agent starts 0.8 s later and talks 1 s
    left = tone(50) + quiet(100)
    right = quiet(50 + 40) + tone(50) + quiet(10)
    m = analyse(stereo(left, right))
    assert len(m["reply_latency_s"]) == 1 and abs(m["reply_latency_s"][0] - 0.8) < 0.05


def test_barge_in_stop_time_measured_from_caller_onset():
    # agent talks; caller starts at 1.0 s into it; agent stops 0.3 s later
    right = tone(50) + tone(15) + quiet(60)
    left = quiet(50) + tone(40) + quiet(35)
    m = analyse(stereo(left, right))
    assert len(m["barge_in_stop_s"]) == 1 and abs(m["barge_in_stop_s"][0] - 0.3) < 0.05


def test_stalls_inside_a_reply_and_clipping_are_reported():
    right = tone(30) + quiet(30) + tone(30) + quiet(100)       # 0.6 s hole mid-reply
    left = quiet(len(right) // FRAME)
    m = analyse(stereo(left, right))
    assert m["stalls"] == 1 and m["clipped_fraction"] == 0.0
    loud = stereo(quiet(10), [32767] * FRAME * 10)
    assert analyse(loud)["clipped_fraction"] > 0.9


def test_no_reply_is_reported_as_missing():
    m = analyse(stereo(tone(50) + quiet(200), quiet(250)))
    assert m["reply_latency_s"] == [] and m["unanswered_turns"] == 1
