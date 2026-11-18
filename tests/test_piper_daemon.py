"""Resident Piper + phrase cache (#28, 'feel human'). Pi: per-call piper 2.59 s for a
long sentence; resident piper 1.44 s long / 0.3-0.4 s short (model load ~1.2 s gone).
A fake piper speaks the real --output_dir protocol: one stdin line -> one wav path on stdout."""
import asyncio
import sys
import time

import pytest

from receptionist.audio import CachingTTS, PiperDaemonTTS, rms_mulaw

FAKE = r'''
import sys, time, wave, math, struct, pathlib
out = pathlib.Path(sys.argv[sys.argv.index("--output_dir") + 1]); log = pathlib.Path(sys.argv[-1])
n = 0
for line in sys.stdin:
    text = line.strip(); n += 1
    with log.open("a") as f: f.write(text + "\n")
    if text.startswith("SLOW"): time.sleep(1.0)
    p = out / f"{n}.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 7))) for i in range(22050 // 4)))
    sys.stdout.write(str(p) + "\n"); sys.stdout.flush()
'''


@pytest.fixture
def piper(tmp_path):
    script = tmp_path / "fake_piper.py"; script.write_text(FAKE)
    model = tmp_path / "voice.onnx"; model.write_bytes(b"x")
    log = tmp_path / "said.log"
    return PiperDaemonTTS([sys.executable, str(script)], str(model), extra_args=[str(log)]), log


def run(tts, coro):
    async def go():
        try:
            return await coro
        finally:
            await tts.aclose()
    return asyncio.run(go())


def test_returns_audible_8k_mulaw_and_removes_wav(piper, tmp_path):
    tts, _ = piper
    audio = run(tts, tts.synthesize("Okay."))
    assert abs(len(audio) - 2000) <= 2 and rms_mulaw(audio) > 300       # 0.25 s at 8 kHz
    assert not list(tmp_path.rglob("*.wav"))


def test_one_process_for_many_sentences(piper):
    tts, log = piper

    async def go():
        await tts.synthesize("One.")
        pid = tts.pid
        await tts.synthesize("Two.  Lines\nflattened.")
        return pid, tts.pid
    a, b = run(tts, go())
    assert a == b and log.read_text().splitlines() == ["One.", "Two. Lines flattened."]


def test_barge_in_cancel_keeps_process_and_next_reply_is_not_stale(piper):
    tts, log = piper

    async def go():
        await tts.synthesize("warm")
        pid = tts.pid
        task = asyncio.create_task(tts.synthesize("SLOW old reply"))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        audio = await tts.synthesize("Fresh.")
        return pid, tts.pid, audio
    before, after, audio = run(tts, go())
    assert before == after and len(audio) > 0                       # no 1.2 s model reload
    assert log.read_text().splitlines()[-1] == "Fresh."


def test_empty_text_rejected(piper):
    tts, _ = piper
    with pytest.raises(ValueError):
        run(tts, tts.synthesize("   "))


class CountingTTS:
    def __init__(self): self.calls = []
    async def synthesize(self, text):
        self.calls.append(text); return text.encode()


def test_cache_serves_repeat_phrases_instantly_and_prewarms():
    inner = CountingTTS(); tts = CachingTTS(inner, max_items=2)

    async def go():
        await tts.prewarm(["Okay.", "Say yes or no."])
        a = await tts.synthesize("Okay.")
        b = await tts.synthesize("Say yes or no.")
        await tts.synthesize("Thursday at two.")                    # evicts the least recently used
        await tts.synthesize("Okay.")
        return a, b
    a, b = asyncio.run(go())
    assert a == b"Okay." and b == b"Say yes or no."
    assert inner.calls == ["Okay.", "Say yes or no.", "Thursday at two.", "Okay."]


def test_cache_only_stores_short_fixed_phrases():
    inner = CountingTTS(); tts = CachingTTS(inner, max_chars=20)

    async def go():
        for _ in range(2):
            await tts.synthesize("Your booking code is four seven two nine.")
    asyncio.run(go())
    assert len(inner.calls) == 2                                      # long/variable text never cached
