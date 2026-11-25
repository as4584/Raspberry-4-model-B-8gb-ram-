"""Resident Paradee voice, speaking resident Piper's pipe: one text line on stdin ->
one wav path on stdout. Runs under its own Python 3.12 venv (misaki needs <3.13).

    paradee-venv/bin/python scripts/paradee_daemon.py --voice af_heart --threads 2 \
        --speed 1.0 --model .../paradee_int8.onnx --output_dir /tmp/x
"""
from __future__ import annotations

import argparse
import array
import os
import sys
import wave


def to_pcm16(samples) -> bytes:
    try:
        import numpy as np
        return (np.clip(np.asarray(samples, dtype="float32"), -1, 1) * 32767).astype("<i2").tobytes()
    except ImportError:
        return array.array("h", (int(max(-1.0, min(1.0, x)) * 32767) for x in samples)).tobytes()


def serve(stdin, stdout, synth, out_dir: str, rate: int) -> None:
    n = 0
    for line in stdin:
        text = line.strip()
        if not text:
            continue
        n += 1
        path = os.path.join(out_dir, f"{os.getpid()}-{n}.wav")
        with wave.open(path, "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
            f.writeframes(to_pcm16(synth(text)))
        stdout.write(path + "\n"); stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    from paradee import Paradee, SAMPLE_RATE
    config = os.path.join(os.path.dirname(os.path.dirname(a.model)), "config.json")
    tts = Paradee(model_path=a.model, config_path=config if os.path.isfile(config) else None, threads=a.threads)
    tts("Warm up.", speed=a.speed, voice=a.voice)
    serve(sys.stdin, sys.stdout, lambda t: tts(t, speed=a.speed, voice=a.voice), a.output_dir, SAMPLE_RATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
