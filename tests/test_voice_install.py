"""Real speech in the hardened service. ProtectHome=yes hides ~/opt and ~/models,
so the voice installer copies binaries and models into root-owned
/opt/pi-receptionist/speech and switches the backend with an optional
EnvironmentFile. Mock stays the default when no voice is installed."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNIT = (ROOT / "deploy" / "pi-receptionist.service").read_text()
SCRIPT = ROOT / "scripts" / "pi_install_voice.sh"
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash required")


def test_unit_reads_optional_voice_env_after_mock_default():
    lines = UNIT.splitlines()
    assert "EnvironmentFile=-/etc/pi-receptionist/voice.env" in lines
    assert lines.index("Environment=VOICE_BACKEND=mock") < lines.index("EnvironmentFile=-/etc/pi-receptionist/voice.env")


def plan(*args, uid="1000"):
    env = {**os.environ, "DRY_RUN": "1", "BOOTSTRAP_UID": uid}
    r = subprocess.run([BASH, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)
    return r, "\n".join(l[6:] for l in r.stdout.splitlines() if l.startswith("PLAN: "))


@needs_bash
def test_voice_install_plan_copies_into_root_owned_opt_and_switches_backend():
    r, p = plan("/home/x/opt/whisper/bin", "/home/x/models/ggml-base.en.bin",
                "/home/x/opt/piper", "/home/x/models/voice.onnx")
    assert r.returncode == 0, r.stderr
    assert "/opt/pi-receptionist/speech" in p
    assert "chown -R root:root /opt/pi-receptionist/speech" in p
    assert "VOICE_BACKEND=local" in p
    assert "WHISPER_CLI=/opt/pi-receptionist/speech/whisper/whisper-cli" in p
    assert "WHISPER_MODEL=/opt/pi-receptionist/speech/models/ggml-base.en.bin" in p
    assert "PIPER_CLI=/opt/pi-receptionist/speech/piper/piper" in p
    assert "PIPER_MODEL=/opt/pi-receptionist/speech/models/voice.onnx" in p
    assert "/home/" not in p.split("voice.env", 1)[1].split("\n", 1)[0]   # env file never points at a home dir
    assert p.rindex("systemctl restart pi-receptionist.service") > p.index("voice.env")
    assert "voice_backend" in p                                        # healthz check confirms the switch


@needs_bash
def test_voice_off_returns_to_mock():
    r, p = plan("--off")
    assert r.returncode == 0, r.stderr
    assert "VOICE_BACKEND=mock" in p and "systemctl restart pi-receptionist.service" in p


@needs_bash
def test_voice_install_needs_all_four_paths_and_non_root():
    r, _ = plan("/a", "/b")
    assert r.returncode == 60 and "FAIL:" in r.stderr
    r, _ = plan("/a", "/b", "/c", "/d", uid="0")
    assert r.returncode == 61 and "FAIL:" in r.stderr


@needs_bash
def test_voice_env_points_loader_at_copied_whisper_libs():
    # Found on the Pi: whisper-cli's build-time library path is under ~/, which
    # ProtectHome hides, so it died with "libwhisper.so.1: cannot open".
    _, p = plan("/home/x/w", "/home/x/m.bin", "/home/x/p", "/home/x/v.onnx")
    assert "LD_LIBRARY_PATH=/opt/pi-receptionist/speech/whisper" in p


@needs_bash
def test_voice_install_smoke_tests_binaries_inside_the_service_sandbox():
    _, p = plan("/home/x/w", "/home/x/m.bin", "/home/x/p", "/home/x/v.onnx")
    smoke = [l for l in p.splitlines() if "systemd-run" in l]
    assert smoke, "no sandboxed smoke test"
    s = " ".join(smoke)
    for prop in ("User=receptionist", "ProtectHome=yes", "ProtectSystem=strict", "PrivateTmp=yes"):
        assert prop in s, prop
    assert "whisper-cli" in s and "piper" in s
    assert p.index("systemd-run") < p.rindex("systemctl restart")   # verify before switching
