"""Silero voice detection in the call engine (bench on the Pi: real call noise WER 32% -> 13%,
fan noise at 0 dB 80% -> 7%, no never-ending turns). A turn needs BOTH a loud line and a human
voice; the loudness margin adapts to whether the background is people talking or plain noise."""
import asyncio
import os

import pytest

from receptionist.audio import MockTTS
from receptionist.booking import BookingStore
from receptionist.telephony import AudioIn, CallEngine, CallStart
from test_call_engine import LOUD, QUIET, FakeTransport, RecordingASR


class ScriptedVAD:
    """Per-call stream: says 'human' exactly for the frames listed as speech."""
    def __init__(self, speech_frames): self.speech, self.i = set(speech_frames), 0
    def feed(self, frame):
        p = 1.0 if self.i in self.speech else 0.0
        self.i += 1
        return p


def run_with(tmp_path, frames, speech_frames, greet=False):
    asr = RecordingASR()
    engine = CallEngine(BookingStore(str(tmp_path / "v.sqlite")), asr=asr, tts=MockTTS(), greet=greet,
                        vad_factory=lambda: ScriptedVAD(speech_frames))
    script = [CallStart("C1", None), *[AudioIn(f) for f in frames], 0.5]
    asyncio.run(engine.run(FakeTransport(script)))
    return asr.packets


def test_loud_noise_that_is_not_a_voice_never_starts_a_turn(tmp_path):
    # Fan / traffic: loud, but no human in it.
    assert run_with(tmp_path, [LOUD] * 150, speech_frames=[]) == []


def test_turn_ends_when_the_voice_stops_even_if_the_line_stays_loud(tmp_path):
    # Fan at 0 dB: the loudness rule never saw a pause (13 s turns, whisper timed out).
    packets = run_with(tmp_path, [LOUD] * 200, speech_frames=range(0, 30))
    assert packets and len(packets[0]) <= (30 + 15 + 20) * 160


def test_quiet_frames_end_the_turn_as_before(tmp_path):
    packets = run_with(tmp_path, [LOUD] * 20 + [QUIET] * 30, speech_frames=range(0, 50))
    assert packets and len(packets[0]) <= (20 + 15 + 15) * 160


def test_no_turn_runs_past_eight_seconds(tmp_path):
    # Background talkers keep the voice detector on: cap the turn instead of hanging.
    packets = run_with(tmp_path, [LOUD] * 600, speech_frames=range(0, 600))
    assert packets and len(packets[0]) <= 8 * 8000 + 2400


def test_without_a_vad_the_engine_behaves_as_before(tmp_path):
    asr = RecordingASR()
    engine = CallEngine(BookingStore(str(tmp_path / "n.sqlite")), asr=asr, tts=MockTTS())
    asyncio.run(engine.run(FakeTransport([CallStart("C1", None), *[AudioIn(LOUD)] * 150, 0.5])))
    assert asr.packets                                 # loud line alone still makes a turn


MODEL = os.environ.get("SILERO_VAD_MODEL", "/var/tmp/vadbench/silero_vad.onnx")


@pytest.mark.skipif(not os.path.exists(MODEL), reason="Silero model not present")
def test_real_silero_hears_silence_as_not_speech():
    from receptionist.vad import SileroVAD
    stream = SileroVAD(MODEL).stream()
    probs = [stream.feed(b"\xff" * 160) for _ in range(50)]
    assert max(probs) < 0.2


def test_server_builds_a_silero_factory_only_when_asked(monkeypatch):
    from receptionist.server import select_vad
    assert select_vad({}) is None
    with pytest.raises(ValueError):
        select_vad({"VAD_ENGINE": "silero"})                       # model path required
    monkeypatch.setattr("receptionist.vad.SileroVAD", lambda path: type("S", (), {"stream": lambda self: path})())
    factory = select_vad({"VAD_ENGINE": "silero", "SILERO_VAD_MODEL": "/m/silero_vad.onnx"})
    assert factory() == "/m/silero_vad.onnx"
