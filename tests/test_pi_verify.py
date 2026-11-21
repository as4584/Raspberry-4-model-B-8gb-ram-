"""verify script (adapted from the owner's kit verify.sh): same checks, our paths, and the
kit's Stasis loopback (no Stasis app in our pipeline) replaced by a real SIPp booking call."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = (ROOT / "scripts" / "pi_verify.sh").read_text()


def test_keeps_every_kit_check_category():
    for needle in ("pi-receptionist-secrets", "tmpfs", "640", "password *= *[^$ ]", "__probe",
                   "--config dev", "127.0.0.1:8088", "tailscale0", "tailscale status",
                   "transport-tls.*tls", "telnyx-reg.*Registered", "media_encryption *: *sdes",
                   "uac_echo.xml", "authorization: digest", "dp\.st\."):
        assert needle in V, needle


def test_stasis_check_replaced_by_real_booking_call():
    assert "stasis" not in V and "sipp_call_test.py --calls 1" in V and "SIPP_CALL_OK" in V
    assert "/etc/pi-receptionist/doppler.env" in V and "/etc/receptionist" not in V


def test_never_prints_values():
    assert "doppler secrets get" not in V and "cat /run/asterisk-secrets" not in V
