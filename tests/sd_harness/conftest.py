"""Shared pytest fixtures for the V1 StreamDouble transport harness."""
import pytest
from fastapi.testclient import TestClient
from receptionist.server import Settings, create_app
from receptionist.audio import MockASR, MockTTS
from receptionist.booking import BookingStore
from tests.sd_harness.helpers import build_app


@pytest.fixture
def app(tmp_path):
    return build_app(tmp_path, transcripts=["book 2027-12-10 14:00", "yes"])


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def db_app(tmp_path):
    db_path = str(tmp_path / "oracle.sqlite")
    app = create_app(
        Settings(db_path=db_path, dev_allow_unsigned=True,
                 wss_url="wss://localhost/twilio/media"),
        asr_factory=lambda: MockASR(["book 2027-12-10 14:00", "yes"]),
        tts=MockTTS(),
    )
    return app, BookingStore(db_path)
