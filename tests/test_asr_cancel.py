"""S5 backpressure: a barge-in cancels the ASR task. The whisper process must
die with it, or a caller talking in bursts stacks 4-thread whisper processes on
a 4-core Pi and every reply slows down."""
import asyncio
import sys
import time

import pytest

from receptionist.audio import WhisperCppASR

SLOW = "import sys,time,pathlib; d=pathlib.Path(sys.argv[1]); (d/'started').touch(); time.sleep({s}); (d/'finished').touch(); print('hi')"


class FakeWhisper(WhisperCppASR):
    def __init__(self, tmp_path, sleep_s, timeout=40):
        model = tmp_path / "m.bin"
        model.write_bytes(b"x")
        super().__init__("unused", str(model), timeout=timeout)
        self.dir, self.sleep_s = tmp_path, sleep_s

    def args(self, wav, seconds=None):
        return [sys.executable, "-c", SLOW.format(s=self.sleep_s), str(self.dir)]


def _wait_for(path, limit=10):
    end = time.monotonic() + limit
    while not path.exists():
        assert time.monotonic() < end, f"{path.name} never appeared"
        time.sleep(0.05)


def test_cancel_kills_whisper_process(tmp_path):
    asr = FakeWhisper(tmp_path, sleep_s=2)

    async def scenario():
        task = asyncio.create_task(asr.transcribe(b"\xff" * 800))
        while not (tmp_path / "started").exists():
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    time.sleep(3)                                   # longer than the child's sleep
    assert not (tmp_path / "finished").exists(), "whisper kept running after cancel"


def test_timeout_kills_whisper_process(tmp_path):
    asr = FakeWhisper(tmp_path, sleep_s=3, timeout=1)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(asr.transcribe(b"\xff" * 800))
    assert time.monotonic() - t0 < 2.5
    time.sleep(3)
    assert not (tmp_path / "finished").exists(), "whisper kept running after timeout"


def test_normal_transcription_still_returns_text(tmp_path):
    assert asyncio.run(FakeWhisper(tmp_path, sleep_s=0).transcribe(b"\xff" * 800)) == "hi"
