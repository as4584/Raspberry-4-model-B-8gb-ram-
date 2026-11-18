"""Security invariant: with signature checks on and no TWILIO_AUTH_TOKEN, every
Twilio request is refused, including one signed with an empty HMAC key, which
anyone can compute. Already true at 976c95c; pinned so a refactor can't
silently open it while the Pi runs without Twilio credentials."""
import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from receptionist.server import Settings, create_app, valid_signature

URL = "https://example.invalid/twilio/voice"


def empty_key_sig(url, params):
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(b"", data.encode(), hashlib.sha1).digest()).decode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    app = create_app(Settings(db_path=str(tmp_path / "t.sqlite"), dev_allow_unsigned=False,
                              public_url="https://example.invalid", auth_token=""))
    return TestClient(app)


def test_empty_key_forgery_rejected_on_voice_webhook(client):
    params = {"CallSid": "CAforged"}
    r = client.post("/twilio/voice", data=params, headers={"X-Twilio-Signature": empty_key_sig(URL, params)})
    assert r.status_code == 403


def test_unsigned_voice_webhook_rejected(client):
    assert client.post("/twilio/voice", data={"CallSid": "CA1"}).status_code == 403


def test_simulate_endpoint_hidden_in_production(client):
    assert client.post("/simulate/text", json={"session_id": "s", "text": "hi"}).status_code == 404


@pytest.mark.parametrize("token,actual", [("", "anything"), ("secret", None), ("secret", "")])
def test_valid_signature_fails_closed(token, actual):
    assert valid_signature(URL, {}, actual, token) is False


def test_valid_signature_accepts_correct_hmac():
    params = {"B": "2", "A": "1"}
    data = URL + "A1B2"
    sig = base64.b64encode(hmac.new(b"secret", data.encode(), hashlib.sha1).digest()).decode()
    assert valid_signature(URL, params, sig, "secret") is True
    assert valid_signature(URL, params, sig, "other") is False
