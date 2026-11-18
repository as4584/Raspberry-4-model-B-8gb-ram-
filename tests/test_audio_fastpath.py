"""S4/S6: table-driven audio codecs must be bit-identical to the scalar G.711
reference and much faster (every 20 ms frame of every call runs rms_mulaw;
every utterance runs mulaw->pcm16->16 kHz before whisper). Exhaustive
equivalence over all 256 u-law codes and all 65,536 PCM16 values."""
import random
import struct
import time

import pytest

from receptionist import audio


def ref_pcm16_from_mulaw(data):
    return b"".join(struct.pack("<h", audio.ulaw_to_pcm(b)) for b in data)


def ref_mulaw_from_pcm16(data):
    return bytes(audio.pcm_to_ulaw(v) for (v,) in struct.iter_unpack("<h", data[: len(data) // 2 * 2]))


def ref_rms(data):
    import math
    return math.sqrt(sum(audio.ulaw_to_pcm(v) ** 2 for v in data) / len(data)) if data else 0.0


ALL_ULAW = bytes(range(256))
ALL_PCM = struct.pack("<65536h", *range(-32768, 32768))


def test_mulaw_to_pcm_exhaustive():
    assert audio.pcm16_from_mulaw(ALL_ULAW) == ref_pcm16_from_mulaw(ALL_ULAW)


def test_pcm_to_mulaw_exhaustive():
    assert audio.mulaw_from_pcm16(ALL_PCM) == ref_mulaw_from_pcm16(ALL_PCM)


def test_odd_length_pcm_ignores_trailing_byte():
    assert audio.mulaw_from_pcm16(ALL_PCM[:7]) == ref_mulaw_from_pcm16(ALL_PCM[:7])


def test_rms_matches_reference():
    rng = random.Random(7)
    for n in (0, 1, 160, 4000):
        data = bytes(rng.randrange(256) for _ in range(n))
        assert audio.rms_mulaw(data) == pytest.approx(ref_rms(data), rel=1e-12)


@pytest.mark.parametrize("src,dst", [(8000, 16000), (16000, 8000), (22050, 16000), (8000, 8000)])
def test_resample_unchanged(src, dst):
    rng = random.Random(src + dst)
    pcm = struct.pack(f"<{800}h", *(rng.randrange(-32768, 32768) for _ in range(800)))
    raw = [v for (v,) in struct.iter_unpack("<h", pcm)]
    out = []
    for j in range(round(len(raw) * dst / src)):
        x = j * src / dst
        left = min(int(x), len(raw) - 1)
        right = min(left + 1, len(raw) - 1)
        out.append(max(-32768, min(32767, round(raw[left] + (raw[right] - raw[left]) * (x - left)))))
    expect = pcm if src == dst else struct.pack("<" + "h" * len(out), *out)
    assert audio.resample_pcm16(pcm, src, dst) == expect


def _best(fn, arg, n=5):
    t = []
    for _ in range(n):
        s = time.perf_counter()
        fn(arg)
        t.append(time.perf_counter() - s)
    return min(t)


def test_fast_paths_are_at_least_5x_faster_than_reference():
    two_s = bytes(random.Random(1).randrange(256) for _ in range(16000))   # 2 s of 8 kHz u-law
    pcm = ref_pcm16_from_mulaw(two_s)
    assert _best(ref_pcm16_from_mulaw, two_s) / _best(audio.pcm16_from_mulaw, two_s) >= 5
    assert _best(ref_mulaw_from_pcm16, pcm) / _best(audio.mulaw_from_pcm16, pcm) >= 5
    assert _best(ref_rms, two_s) / _best(audio.rms_mulaw, two_s) >= 5
