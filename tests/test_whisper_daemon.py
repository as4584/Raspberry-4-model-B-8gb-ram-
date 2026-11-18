"""Resident whisper (native/whisper_daemon.cpp) behind an async adapter (#28).
Pi evidence: base.en-q5_1, short window, greedy, no fallback, fresh state per
call -> 32/40 exact, p50 0.72 s, max 0.89 s (vs 7.1 s with whisper-cli).
Tests drive a fake daemon speaking the same line protocol."""
import asyncio
import math
import sys
import time
from pathlib import Path

import pytest

from receptionist.audio import WhisperDaemonASR, audio_ctx_for

FAKE = r'''
import sys, time, pathlib
log = pathlib.Path(sys.argv[1])
sys.stdout.write("READY\n"); sys.stdout.flush()
for line in sys.stdin:
    path, ac, mt = line.rstrip("\n").split("\t")
    raw = pathlib.Path(path).read_bytes()
    with log.open("a") as f: f.write(f"{len(raw)} {ac} {mt}\n")
    if len(raw) == 32000 * 3: time.sleep(5)          # 3 s of audio: "slow" utterance
    sys.stdout.write(" Book December 10 at 2 p.m. Book December 10 at 2 p.m.\n"); sys.stdout.flush()
'''


@pytest.fixture
def daemon(tmp_path):
    script = tmp_path / "fake_daemon.py"
    script.write_text(FAKE)
    log = tmp_path / "calls.log"
    return WhisperDaemonASR([sys.executable, str(script), str(log)]), log


def test_transcribes_and_cleans_output(daemon):
    asr, _ = daemon
    assert asyncio.run(asr.transcribe(b"\xff" * 8000)) == "Book December 10 at 2 p.m."


def test_sends_16k_pcm_short_window_and_token_cap(daemon):
    asr, log = daemon
    asyncio.run(asr.transcribe(b"\xff" * 16000))                 # 2 s of 8 kHz u-law
    size, ac, mt = log.read_text().split()
    assert int(size) == 2 * 16000 * 2                            # 16 kHz PCM16
    assert int(ac) == audio_ctx_for(2.0)
    assert int(mt) == math.ceil(2.0 * 6) + 4


def test_process_is_reused_across_utterances(daemon):
    asr, log = daemon

    async def two():
        await asr.transcribe(b"\xff" * 8000)
        pid = asr.pid
        await asr.transcribe(b"\xff" * 8000)
        return pid, asr.pid
    first, second = asyncio.run(two())
    assert first == second and len(log.read_text().splitlines()) == 2


def test_cancel_kills_daemon_and_next_call_respawns(daemon):
    asr, _ = daemon

    async def scenario():
        await asr.transcribe(b"\xff" * 8000)
        old = asr.pid
        task = asyncio.create_task(asr.transcribe(b"\xff" * 24000))   # slow one
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        t0 = time.monotonic()
        text = await asr.transcribe(b"\xff" * 8000)
        return old, asr.pid, text, time.monotonic() - t0
    old, new, text, took = asyncio.run(scenario())
    assert old != new and text == "Book December 10 at 2 p.m." and took < 3


def test_empty_audio_never_hits_daemon(daemon):
    asr, log = daemon
    assert asyncio.run(asr.transcribe(b"")) == "" and not log.exists()
