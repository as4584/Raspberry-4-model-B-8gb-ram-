"""Paradee (8 MB distilled Kokoro voice) as the resident voice: same one-line-in /
one-wav-path-out pipe as resident Piper, so caching, prewarm and barge-in stay unchanged."""
import io
import importlib.util
import wave
from pathlib import Path

import pytest

from receptionist.audio import CachingTTS, PiperDaemonTTS

ENV = {"VOICE_BACKEND": "local", "TTS_ENGINE": "paradee", "PARADEE_PYTHON": "/opt/paradee/bin/python",
       "PARADEE_MODEL": "/m/paradee_int8.onnx"}


def daemon():
    path = Path(__file__).resolve().parents[1] / "scripts" / "paradee_daemon.py"
    spec = importlib.util.spec_from_file_location("paradee_daemon", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod, path


def test_server_selects_resident_paradee_with_safe_defaults():
    from receptionist.dialogue import OPENERS
    from receptionist.server import select_tts
    tts, kind = select_tts(ENV)
    _, script = daemon()
    assert kind == "paradee" and isinstance(tts, CachingTTS) and isinstance(tts.inner, PiperDaemonTTS)
    assert tts.inner.argv == ["/opt/paradee/bin/python", str(script), "--voice", "af_heart", "--threads", "2",
                              "--speed", "1.0"]
    assert tts.inner.model == "/m/paradee_int8.onnx" and tts.prewarm_phrases == OPENERS


@pytest.mark.parametrize("key,bad", [("PARADEE_VOICE", "af_heart; rm -rf /"), ("PARADEE_THREADS", "9"),
                                     ("PARADEE_SPEED", "5")])
def test_paradee_settings_are_validated(key, bad):
    from receptionist.server import select_tts
    with pytest.raises(ValueError):
        select_tts(dict(ENV, **{key: bad}))


def test_piper_stays_the_default_engine():
    from receptionist.server import select_tts
    _, kind = select_tts({"VOICE_BACKEND": "local", "PIPER_MODEL": "/v.onnx", "PIPER_RESIDENT": "1"})
    assert kind == "resident"


def test_daemon_answers_each_line_with_a_mono_pcm16_wav(tmp_path):
    mod, _ = daemon()
    said = []
    def synth(text):
        said.append(text); return [0.0, 0.5, -0.5, 1.5] * 100          # 1.5 must clip, not wrap
    out = io.StringIO()
    mod.serve(io.StringIO("Hello there.\n\nSecond line.\n"), out, synth, str(tmp_path), rate=24000)
    paths = out.getvalue().split()
    assert said == ["Hello there.", "Second line."] and len(paths) == 2
    with wave.open(paths[0], "rb") as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 24000)
        frames = f.readframes(4)
    import struct
    assert struct.unpack("<4h", frames) == (0, 16383, -16383, 32767)
