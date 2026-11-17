"""S1 access-negative tests — Ramona (Laptop 2).

These tests verify security boundaries that can be checked without real Pi
hardware. Pi-dependent checks (Tailscale reachability, remote pytest) are
in evidence_manifest.md and require hardware.

TDD gate: verified RED by temporarily loosening the server's guards,
then confirmed GREEN against the production server configuration.
"""
import uuid
import pytest
from fastapi.testclient import TestClient
from receptionist.server import Settings, create_app
from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue
from datetime import datetime, timedelta, timezone

START = datetime(2027, 6, 1, 14, 0, 0, tzinfo=timezone.utc)


def prod_app(tmp_path):
    """App in production mode: unsigned requests rejected, simulate/ hidden."""
    return create_app(Settings(
        db_path=str(tmp_path / "prod.sqlite"),
        dev_allow_unsigned=False,
        auth_token="secret",
        public_url="https://example.com",
        wss_url="wss://example.com/twilio/media",
    ))


def dev_app(tmp_path):
    return create_app(Settings(
        db_path=str(tmp_path / "dev.sqlite"),
        dev_allow_unsigned=True,
        wss_url="wss://localhost/twilio/media",
    ))


# ---------------------------------------------------------------------------
# N3: /simulate/text unavailable in production mode
# ---------------------------------------------------------------------------

def test_simulate_endpoint_hidden_in_prod(tmp_path):
    c = TestClient(prod_app(tmp_path))
    r = c.post("/simulate/text", json={"session_id": "x", "text": "book 2027-12-01 14:00"})
    assert r.status_code == 404


def test_simulate_endpoint_available_in_dev(tmp_path):
    c = TestClient(dev_app(tmp_path))
    r = c.post("/simulate/text", json={"session_id": "x", "text": "book 2027-12-01 14:00"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# N4: Unauthorized cancellation with fabricated UUID
# ---------------------------------------------------------------------------

def test_cancel_fabricated_uuid_via_dialogue(tmp_path):
    db = BookingStore(str(tmp_path / "cancel_neg.sqlite"))
    d = Dialogue(db)
    fake = str(uuid.uuid4())
    r = d.handle(f"cancel {fake}", "s1")
    assert r.action == "not_found"
    assert db.active() == []


def test_cancel_another_sessions_booking_requires_reference(tmp_path):
    """Session 2 cannot cancel session 1's booking without the opaque reference."""
    db = BookingStore(str(tmp_path / "cancel_ref.sqlite"))
    d1 = Dialogue(db)
    d1.handle("book 2027-12-01 14:00", "s1")
    r = d1.handle("yes", "s1")
    assert r.action == "booked"
    real_id = r.committed_id

    d2 = Dialogue(db)
    # Attempting with a fabricated id fails
    fake = str(uuid.uuid4())
    assert d2.handle(f"cancel {fake}", "s2").action == "not_found"
    assert len(db.active()) == 1

    # Using the real id succeeds (simulates bearer reference possession)
    assert d2.handle(f"cancel {real_id}", "s2").action == "cancelled"
    assert db.active() == []


# ---------------------------------------------------------------------------
# N5: Cross-call session isolation
# ---------------------------------------------------------------------------

def test_cross_call_dialogue_state_is_independent(tmp_path):
    """Two sessions (calls) must not share pending booking state."""
    db = BookingStore(str(tmp_path / "isolation.sqlite"))
    d1 = Dialogue(db)
    d2 = Dialogue(db)

    # d1 sets up a pending booking
    d1.handle("book 2027-12-01 14:00", "s1")
    assert d1.handle("yes", "s1").action == "booked"

    # d2 says "yes" with no pending — must not create a booking
    r = d2.handle("yes", "s2")
    assert r.action == "reply"  # "There is no pending booking to confirm."
    assert len(db.active()) == 1  # only d1's booking


def test_cross_call_no_cancel_pollution(tmp_path):
    """d1 cancel command must not affect d2's pending state."""
    db = BookingStore(str(tmp_path / "cancel_isolation.sqlite"))
    d1 = Dialogue(db)
    d2 = Dialogue(db)

    d2.handle("book 2027-12-01 15:00", "s2")  # d2 has pending
    d1.handle("no", "s1")  # d1 says no (clears d1's pending)

    # d2's pending is unaffected
    assert d2.handle("yes", "s2").action == "booked"


# ---------------------------------------------------------------------------
# Webhook: unsigned request rejected in production mode
# ---------------------------------------------------------------------------

def test_unsigned_webhook_rejected_in_prod(tmp_path):
    c = TestClient(prod_app(tmp_path))
    r = c.post("/twilio/voice", data={"CallSid": "X"})
    assert r.status_code == 403


def test_healthz_always_public(tmp_path):
    """Health check must respond regardless of auth mode."""
    c = TestClient(prod_app(tmp_path))
    assert c.get("/healthz").status_code == 200
