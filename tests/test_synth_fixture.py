"""Caller-voice fixtures: Piper sometimes clips a short word ("Yes." -> 0.18 s "Ye").
A real caller says the whole word, so the test keeps the longest of a few takes."""
import os
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import asr_sweep  # noqa: E402


def test_short_phrase_keeps_longest_take(tmp_path, monkeypatch):
    lengths = iter([1800, 4100, 3000, 4000, 2000])       # samples at 8 kHz: the clipped take first
    def fake_run(args, input, **kw):
        out = Path(args[args.index("--output_file") + 1])
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"\x10\x00" * next(lengths))
    monkeypatch.setattr(asr_sweep.subprocess, "run", fake_run)
    monkeypatch.setenv("PIPER_CLI", "piper"); monkeypatch.setenv("PIPER_MODEL", "m.onnx")
    assert len(asr_sweep.synth("Yes.", tmp_path)) == 4100


def test_cache_is_per_voice(tmp_path, monkeypatch):
    calls = []
    def fake_run(args, input, **kw):
        calls.append(args)
        out = Path(args[args.index("--output_file") + 1])
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"\x10\x00" * 8000)
    monkeypatch.setattr(asr_sweep.subprocess, "run", fake_run)
    monkeypatch.setenv("PIPER_CLI", "piper")
    monkeypatch.setenv("PIPER_MODEL", "a.onnx"); asr_sweep.synth("Book it please today.", tmp_path)
    n = len(calls)
    monkeypatch.setenv("PIPER_MODEL", "b.onnx"); asr_sweep.synth("Book it please today.", tmp_path)
    assert len(calls) > n
