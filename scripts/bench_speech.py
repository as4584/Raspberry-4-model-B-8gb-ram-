"""S4/S6 speech phase benchmark on the Pi: Piper TTS -> 8 kHz mu-law -> whisper.cpp ASR.

Writes one JSON object per run (warmups flagged) to stdout. Each row carries the Pi
model, git SHA, thread count, temperature and get_throttled; rows taken while
throttled != 0x0 are marked valid=false. MockTTS is never used: silent Piper output
raises in PiperTTS and aborts the run.

  python scripts/bench_speech.py --runs 10 --warmups 3 > docs/evidence/s4-speech-pi4.jsonl
"""
import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from receptionist.audio import AUDIBLE_RMS, PiperTTS, WhisperCppASR, rms_mulaw

PHRASE = 'Book December tenth at two PM.'


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=10)
    ap.add_argument('--warmups', type=int, default=3)
    ap.add_argument('--threads', type=int, default=4)
    a = ap.parse_args()
    wm = os.environ['WHISPER_MODEL']
    tts = PiperTTS(os.environ['PIPER_CLI'], os.environ['PIPER_MODEL'])
    asr = WhisperCppASR(os.environ['WHISPER_CLI'], wm, threads=a.threads)
    static = {
        'pi_model': Path('/proc/device-tree/model').read_text(errors='ignore').strip('\x00\n'),
        'git_sha': sh('git', 'rev-parse', 'HEAD'),
        'whisper_model': Path(wm).name, 'piper_model': Path(os.environ['PIPER_MODEL']).name,
        'whisper_version': 'v1.9.5', 'piper_version': '2023.11.14-2', 'threads': a.threads,
    }
    for i in range(a.warmups + a.runs):
        before = sh('vcgencmd', 'get_throttled').split('=')[-1]
        t0 = time.perf_counter(); speech = asyncio.run(tts.synthesize(PHRASE)); t1 = time.perf_counter()
        text = asyncio.run(asr.transcribe(speech)); t2 = time.perf_counter()
        after = sh('vcgencmd', 'get_throttled').split('=')[-1]
        audio_s = len(speech) / 8000
        rms = rms_mulaw(speech)
        print(json.dumps({**static, 'run': i, 'warmup': i < a.warmups,
                          'tts_s': round(t1 - t0, 3), 'asr_s': round(t2 - t1, 3), 'audio_s': audio_s,
                          'tts_rtf': round((t1 - t0) / audio_s, 3), 'asr_rtf': round((t2 - t1) / audio_s, 3),
                          'rms': round(rms, 1), 'audible': rms > AUDIBLE_RMS, 'transcript': text,
                          'temp': sh('vcgencmd', 'measure_temp').split('=')[-1],
                          'throttled_before': before, 'throttled_after': after,
                          'valid': before == after == '0x0'}), flush=True)


if __name__ == '__main__':
    main()
