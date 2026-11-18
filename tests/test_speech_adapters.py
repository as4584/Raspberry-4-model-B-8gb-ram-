"""S4: argument construction and audio conversion of the local speech adapters.

Fake binaries only (subprocess.run is replaced). Real whisper.cpp/Piper runs live in
tests_pi/test_speech_real.py, which the Pi runs explicitly and which FAILS when the
binaries are not configured.
"""
import asyncio
import math
import struct
import wave
from pathlib import Path

import pytest

from receptionist import audio
from receptionist.audio import PiperTTS, WhisperCppASR, mulaw_from_pcm16, rms_mulaw


def tone_pcm16(hz=440, seconds=0.5, rate=8000, amp=8000):
    n = int(rate * seconds)
    return struct.pack('<' + 'h' * n, *(int(amp * math.sin(2 * math.pi * hz * i / rate)) for i in range(n)))


class Completed:
    def __init__(self, stdout='', stderr=''):
        self.stdout, self.stderr, self.returncode = stdout, stderr, 0


@pytest.fixture
def model(tmp_path):
    p = tmp_path / 'model.bin'
    p.write_bytes(b'x')
    return str(p)


def capture_whisper(monkeypatch, stdout):
    calls = []

    def fake_run(args, **kw):
        wav = Path(args[args.index('-f') + 1])
        with wave.open(str(wav), 'rb') as f:
            calls.append({'args': args, 'kw': kw, 'rate': f.getframerate(), 'ch': f.getnchannels(),
                          'width': f.getsampwidth(), 'frames': f.getnframes()})
        return Completed(stdout)
    monkeypatch.setattr(audio.subprocess, 'run', fake_run)
    return calls


# --- whisper.cpp (pinned v1.9.5 whisper-cli) ---------------------------------------

def test_whisper_args_match_pinned_cli(monkeypatch, model):
    calls = capture_whisper(monkeypatch, ' book December tenth at two pm\n')
    asr = WhisperCppASR('/opt/whisper-cli', model, threads=3)
    text = asyncio.run(asr.transcribe(mulaw_from_pcm16(tone_pcm16())))
    a = calls[0]['args']
    assert a[0] == '/opt/whisper-cli'
    assert a[a.index('-m') + 1] == model
    assert a[a.index('-t') + 1] == '3'
    assert a[a.index('-l') + 1] == 'en'
    assert '-nt' in a and '-np' in a  # no timestamps, no progress/system prints on stdout
    assert text == 'book December tenth at two pm'


def test_whisper_receives_16k_mono_pcm16_wav_of_same_duration(monkeypatch, model):
    calls = capture_whisper(monkeypatch, 'x')
    asyncio.run(WhisperCppASR('w', model).transcribe(mulaw_from_pcm16(tone_pcm16(seconds=0.5))))
    c = calls[0]
    assert (c['rate'], c['ch'], c['width']) == (16000, 1, 2)
    assert c['frames'] == 8000  # 0.5 s at 16 kHz


def test_whisper_joins_multiline_output_and_drops_blank_audio_markers(monkeypatch, model):
    capture_whisper(monkeypatch, ' [BLANK_AUDIO]\n book December\n tenth at two pm \n')
    assert asyncio.run(WhisperCppASR('w', model).transcribe(b'\x00' * 160)) == 'book December tenth at two pm'


def test_whisper_blank_audio_only_is_empty(monkeypatch, model):
    capture_whisper(monkeypatch, '[BLANK_AUDIO]\n')
    assert asyncio.run(WhisperCppASR('w', model).transcribe(b'\x00' * 160)) == ''


def test_whisper_empty_input_never_runs_binary(monkeypatch, model):
    calls = capture_whisper(monkeypatch, 'hallucination')
    assert asyncio.run(WhisperCppASR('w', model).transcribe(b'')) == ''
    assert calls == []


def test_whisper_has_timeout_and_check(monkeypatch, model):
    calls = capture_whisper(monkeypatch, 'x')
    asyncio.run(WhisperCppASR('w', model, timeout=7).transcribe(b'\x00' * 160))
    assert calls[0]['kw']['timeout'] == 7 and calls[0]['kw']['check'] is True


def test_whisper_default_threads_is_four():
    assert WhisperCppASR('w', 'm').threads == 4


# --- Piper (pinned rhasspy/piper 2023.11.14-2) -------------------------------------

def fake_piper(monkeypatch, rate=22050, seconds=0.5, channels=1, width=2, silent=False):
    calls = []

    def fake_run(args, **kw):
        # Like real subprocess: str input without text=True raises TypeError (bug seen on Pi).
        if isinstance(kw.get('input'), str) and not (kw.get('text') or kw.get('encoding')):
            raise TypeError('memoryview: a bytes-like object is required, not str')
        calls.append({'args': args, 'kw': kw})
        out = args[args.index('--output_file') + 1]
        n = int(rate * seconds)
        pcm = b'\x00\x00' * n if silent else tone_pcm16(rate=rate, seconds=seconds)
        with wave.open(out, 'wb') as f:
            f.setnchannels(channels); f.setsampwidth(width); f.setframerate(rate)
            f.writeframes(pcm if (channels, width) == (1, 2) else pcm * channels)
        return Completed()
    monkeypatch.setattr(audio.subprocess, 'run', fake_run)
    return calls


def test_piper_args_match_pinned_cli(monkeypatch, model):
    calls = fake_piper(monkeypatch)
    asyncio.run(PiperTTS('/opt/piper/piper', model).synthesize('Hello there.'))
    a, kw = calls[0]['args'], calls[0]['kw']
    assert a[0] == '/opt/piper/piper'
    assert a[a.index('--model') + 1] == model
    assert '--output_file' in a
    assert kw['input'] == b'Hello there.\n'  # piper reads one utterance per stdin line
    assert kw['check'] is True and kw['timeout'] == 35


def test_piper_text_newlines_are_flattened_to_one_utterance(monkeypatch, model):
    calls = fake_piper(monkeypatch)
    asyncio.run(PiperTTS('p', model).synthesize('Line one.\nLine two.'))
    assert calls[0]['kw']['input'] == b'Line one. Line two.\n'


def test_piper_22k_output_becomes_8k_mulaw_with_same_duration_and_sound(monkeypatch, model):
    fake_piper(monkeypatch, rate=22050, seconds=0.5)
    out = asyncio.run(PiperTTS('p', model).synthesize('hi'))
    assert abs(len(out) - 4000) <= 1  # 0.5 s at 8 kHz, 1 byte/sample
    assert rms_mulaw(out) > 1000


def test_piper_rejects_silent_output(monkeypatch, model):
    fake_piper(monkeypatch, silent=True)
    with pytest.raises(ValueError, match='silent'):
        asyncio.run(PiperTTS('p', model).synthesize('hi'))


def test_piper_rejects_stereo(monkeypatch, model):
    fake_piper(monkeypatch, channels=2)
    with pytest.raises(ValueError):
        asyncio.run(PiperTTS('p', model).synthesize('hi'))


def test_piper_empty_text_rejected_without_running(monkeypatch, model):
    calls = fake_piper(monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(PiperTTS('p', model).synthesize('   '))
    assert calls == []


def test_mocktts_is_silent_and_would_fail_audibility_threshold():
    assert rms_mulaw(asyncio.run(audio.MockTTS().synthesize('x'))) < audio.AUDIBLE_RMS


def test_piper_non_ascii_text_is_utf8_encoded(monkeypatch, model):
    calls = fake_piper(monkeypatch)
    asyncio.run(PiperTTS('p', model).synthesize('Café at 2 pm'))
    assert calls[0]['kw']['input'] == 'Café at 2 pm\n'.encode('utf-8')


# --- encoder window sized to the utterance (PI4 2026-11-18) --------------------
# whisper's encoder always processes a 30 s window (audio_ctx 1500, 50/s). For a
# 2 s utterance on the Pi: encode 11.65 s -> 1.06 s with -ac 160, same transcript.

@pytest.mark.parametrize("seconds,expect", [(0.5, 128), (2.0, 164), (3.2, 224), (10.0, 564), (30.0, 1500), (40.0, 1500)])
def test_audio_ctx_scales_with_utterance_length(seconds, expect):
    assert audio.audio_ctx_for(seconds) == expect


# Default flipped after the PI4 ASR sweep (#28, 320 runs): the adaptive window
# caused decoding runaways on 13-45% of phrases (p95 69 s, one 120 s timeout);
# the full window had 0 runaways. Adaptive stays available as an opt-in.
def test_whisper_uses_full_window_by_default(monkeypatch, model):
    calls = capture_whisper(monkeypatch, 'x')
    asyncio.run(WhisperCppASR('w', model).transcribe(mulaw_from_pcm16(tone_pcm16(seconds=2.0))))
    assert '-ac' not in calls[0]['args']


def test_whisper_adaptive_window_is_opt_in(monkeypatch, model):
    calls = capture_whisper(monkeypatch, 'x')
    asyncio.run(WhisperCppASR('w', model, adaptive_ctx=True).transcribe(mulaw_from_pcm16(tone_pcm16(seconds=2.0))))
    a = calls[0]['args']
    assert a[a.index('-ac') + 1] == str(audio.audio_ctx_for(2.0))


# --- repetition guard (PI4 2026-11-18) -------------------------------------------
# With a shortened encoder window (-ac 256) whisper deterministically repeated the
# caller's sentence into the padding: "Book December 10 at 2 p.m. Book December 10
# at 2 p.m." (never at -ac 1500 or 176; -mc 0 did not help).

@pytest.mark.parametrize("raw,expect", [
    (" Book December 10 at 2 p.m. Book December 10 at 2 p.m.\n", "Book December 10 at 2 p.m."),
    (" Book December 10 at 2 p.m.\n Book December 10 at 2 p.m.\n", "Book December 10 at 2 p.m."),
    (" Yes. Yes. Yes.\n", "Yes."),
    (" book december tenth book december tenth\n", "book december tenth"),
    (" Cancel it. Actually, book it.\n", "Cancel it. Actually, book it."),
    (" Book at two. Book at three.\n", "Book at two. Book at three."),
    ("[BLANK_AUDIO]\n", ""),
])
def test_whisper_output_collapses_repeated_sentences(raw, expect):
    assert audio.clean_whisper_output(raw) == expect
