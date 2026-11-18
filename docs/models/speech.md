# Local speech components (S4)

Everything runs offline on the Pi under `~/opt` and `~/models` (user space). Installed by
`scripts/pi_install_speech.sh`, which checks SHA256 of every download and the whisper.cpp
commit. Binaries and models are never committed.

| Component | Version / file | Source | Licence | SHA256 (published) |
|---|---|---|---|---|
| whisper.cpp `whisper-cli` | tag `v1.9.5`, commit `d1be6fde11ac6e0407606b4e42fe72d34add8037`, built on the Pi (Release, `-j 3`) | https://github.com/ggml-org/whisper.cpp | MIT | binary hash: see Pi evidence |
| ggml-tiny.en | `ggml-tiny.en.bin` 77,704,715 B | https://huggingface.co/ggerganov/whisper.cpp | MIT (OpenAI Whisper weights) | `921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f` |
| ggml-base.en | `ggml-base.en.bin` 147,964,211 B | same | MIT | `a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002` |
| ggml-base.en q5_1 | `ggml-base.en-q5_1.bin` 59,721,011 B | same | MIT | `4baf70dd0d7c4247ba2b81fafd9c01005ac77c2f9ef064e00dcf195d0e2fdd2f` |
| Piper | release `2023.11.14-2`, `piper_linux_aarch64.tar.gz` 26,004,717 B | https://github.com/rhasspy/piper/releases/tag/2023.11.14-2 | MIT (repo archived; successor piper1-gpl is GPL-3.0) | `fea0fd2d87c54dbc7078d0f878289f404bd4d6eea6e7444a77835d1537ab88eb` (hashed by us; GitHub publishes none) |
| Voice en_US-lessac-medium | `.onnx` 63,201,294 B + `.onnx.json` | https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium | **Training data: Blizzard 2013 Lessac research licence (non-commercial).** OK for dev/benchmarks; **NOT cleared for a commercial deployment**. Pick a voice with a permissive dataset before S9. | onnx `5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f`; json `efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0` (hashed by us) |

## CLI contract (validated against the pinned sources, tests in `tests/test_speech_adapters.py`)
- `whisper-cli -m MODEL -f 16k_mono_pcm16.wav -t THREADS -l en -nt -np`; transcript on stdout; `[BLANK_AUDIO]` dropped.
- `piper --model VOICE.onnx --output_file out.wav` with one utterance on stdin (newline-terminated); 22,050 Hz mono PCM16, resampled to 8 kHz mu-law. Silent output (RMS < `AUDIBLE_RMS`) is an error.

## Real-binary tests (Pi only, explicit)
`WHISPER_CLI=... WHISPER_MODEL=... PIPER_CLI=... PIPER_MODEL=... python -m pytest -q tests_pi/test_speech_real.py`
is not in the default `testpaths`. Missing configuration FAILS rather than skips.
