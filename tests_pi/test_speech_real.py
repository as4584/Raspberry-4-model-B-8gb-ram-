"""Real whisper.cpp + Piper on the Pi. NOT collected by the default `pytest` run
(testpaths=tests). Run explicitly on the Pi:

    WHISPER_CLI=... WHISPER_MODEL=... PIPER_CLI=... PIPER_MODEL=... \
        python -m pytest -q tests_pi/test_speech_real.py

Missing configuration is a FAILURE here, never a skip.
"""
import asyncio
import os
from pathlib import Path

import pytest

from receptionist.audio import AUDIBLE_RMS, PiperTTS, WhisperCppASR, rms_mulaw

PHRASE = 'Book December tenth at two PM.'


def env(name):
    value = os.environ.get(name, '')
    if not value or not Path(value).is_file():
        pytest.fail(f'{name} must point to an existing file (got {value!r})')
    return value


@pytest.fixture(scope='module')
def tts():
    return PiperTTS(env('PIPER_CLI'), env('PIPER_MODEL'))


@pytest.fixture(scope='module')
def asr():
    return WhisperCppASR(env('WHISPER_CLI'), env('WHISPER_MODEL'), threads=int(os.environ.get('WHISPER_THREADS', '4')))


@pytest.fixture(scope='module')
def spoken(tts):
    return asyncio.run(tts.synthesize(PHRASE))


def test_piper_output_is_audible_8k_mulaw_of_plausible_duration(spoken):
    seconds = len(spoken) / 8000
    assert 0.8 <= seconds <= 6.0, seconds
    assert rms_mulaw(spoken) > AUDIBLE_RMS


def test_whisper_transcribes_piper_mulaw_fixture(asr, spoken):
    text = asyncio.run(asr.transcribe(spoken)).lower()
    # whisper normalises numbers ("10th at 2 p.m."), so accept digits or words.
    assert 'book' in text and 'december' in text, text
    assert ('10' in text or 'tenth' in text) and (' 2' in text or 'two' in text), text
