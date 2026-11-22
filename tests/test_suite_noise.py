"""Robustness harness: caller audio with background noise at a chosen SNR."""
import importlib.util
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
spec = importlib.util.spec_from_file_location("cs", ROOT / "scripts" / "conversation_suite.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

from receptionist.audio import mulaw_from_pcm16, pcm16_from_mulaw  # noqa: E402


def tone(n=8000):
    return mulaw_from_pcm16(struct.pack(f"<{n}h", *(int(8000 * math.sin(i / 5)) for i in range(n))))


def power(mu):
    pcm = pcm16_from_mulaw(mu); s = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    return sum(x * x for x in s) / len(s)


def test_noise_is_mixed_at_the_requested_snr():
    clean = tone()
    noisy = cs.add_noise(clean, snr_db=10, seed=0)
    assert len(noisy) == len(clean)
    pc = pcm16_from_mulaw(clean); pn = pcm16_from_mulaw(noisy)
    c = struct.unpack(f"<{len(pc) // 2}h", pc); n = struct.unpack(f"<{len(pn) // 2}h", pn)
    noise_power = sum((b - a) ** 2 for a, b in zip(c, n)) / len(c)
    snr = 10 * math.log10(power(clean) / noise_power)
    assert 8.5 < snr < 11.5


def test_no_noise_returns_the_audio_unchanged():
    clean = tone()
    assert cs.add_noise(clean, snr_db=None, seed=0) == clean
