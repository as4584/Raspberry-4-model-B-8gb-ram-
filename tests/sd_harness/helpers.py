"""Shared helper functions for the V1 StreamDouble transport harness."""
import base64
from receptionist.server import Settings, create_app
from receptionist.audio import MockASR, MockTTS


def build_app(tmp_path, transcripts=None, tts=None):
    return create_app(
        Settings(
            db_path=str(tmp_path / "sd_test.sqlite"),
            dev_allow_unsigned=True,
            wss_url="wss://localhost/twilio/media",
        ),
        asr_factory=lambda: MockASR(list(transcripts or [])),
        tts=tts or MockTTS(),
    )


def mulaw_frame(value: int = 0xFF, frames: int = 1) -> bytes:
    return bytes([value]) * 160 * frames


def encode(payload: bytes) -> str:
    return base64.b64encode(payload).decode()
