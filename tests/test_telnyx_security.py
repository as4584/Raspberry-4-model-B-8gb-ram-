"""T5 security (from the owner's Telnyx kit, 2026-11-18): TLS 5061 signalling, SRTP-only
media, reject any inbound call that is not fully encrypted, SIPp only on a loopback
transport, Telnyx credentials only in a RAM (/run) include rendered from Doppler."""
import re
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "deploy" / "asterisk"
PJ = (D / "pjsip.conf").read_text()
SEC = (D / "pjsip-secrets.conf.tmpl").read_text()
EXT = (D / "extensions.conf").read_text()


def section(text, name, kind):
    for block in re.split(r"\n(?=\[)", text):
        if block.startswith(f"[{name}]") and re.search(rf"^type\s*=\s*{kind}$", block, re.M):
            return block
    raise AssertionError(f"[{name}] type={kind} missing")


def test_tls_transport_verifies_telnyx():
    t = section(PJ, "transport-tls", "transport")
    for line in ("protocol=tls", "bind=0.0.0.0:5061", "method=tlsv1_2", "verify_server=yes"):
        assert line in t.replace(" ", ""), line


def test_telnyx_endpoint_requires_srtp_and_handles_nat():
    e = section(PJ, "telnyx", "endpoint")
    for line in ("transport=transport-tls", "media_encryption=sdes", "media_encryption_optimistic=no",
                 "rtp_symmetric=yes", "rewrite_contact=yes", "force_rport=yes", "context=from-telnyx"):
        assert line in e.replace(" ", ""), line
    assert "allow=ulaw" in e.replace(" ", "")


def test_sipp_lives_on_a_loopback_only_transport():
    t = section(PJ, "transport-test", "transport")
    assert "bind=127.0.0.1:5070" in t.replace(" ", "")
    assert "0.0.0.0:5060" not in PJ
    assert "match=127.0.0.1" in section(PJ, "sipp", "identify").replace(" ", "")


def test_credentials_only_in_ram_include_with_tls_registration():
    assert "#tryinclude /run/asterisk-secrets/pjsip-secrets.conf" in PJ
    assert "TELNYX" not in PJ                                         # no credential placeholders on disk
    assert "server_uri=sip:sip.telnyx.com:5061;transport=tls" in SEC.replace(" ", "")
    assert "contact_user=${TELNYX_SIP_USERNAME}" in SEC.replace(" ", "")
    assert "password=${TELNYX_SIP_PASSWORD}" in SEC.replace(" ", "")


def test_inbound_requires_srtp_after_answer_and_tls_by_transport():
    # Pi source dive (2026-11-21): CHANNEL(pjsip,secure) inspects the dialog *target URI*
    # (Telnyx's Contact, no transport=tls) and read 0 on a real TLS call; CHANNEL(rtp,secure)
    # is empty until media is negotiated at Answer. So: TLS is enforced by the endpoint's
    # only transport, SRTP by media_encryption=sdes (no fallback), and the dialplan checks
    # SRTP *after* Answer and hangs up otherwise.
    tel = EXT[EXT.index("[from-telnyx]"):].split("\n[")[0]
    assert "CHANNEL(pjsip,secure)" not in tel and "secure_signaling)" not in EXT
    ans = tel.index("Answer()")
    chk = tel.index('GotoIf($["${CHANNEL(rtp,secure)}" = "1"]?ok)')
    assert ans < chk < tel.index("Hangup(") < tel.index("Goto(receptionist,s,1)")
    e = section(PJ, "telnyx", "endpoint").replace(" ", "")
    assert "transport=transport-tls" in e and "media_encryption=sdes" in e and "media_encryption_optimistic=no" in e
    assert "bind=127.0.0.1:5070" in section(PJ, "transport-test", "transport").replace(" ", "")


def test_rtp_port_range_matches_firewall_plan():
    rtp = (D / "rtp.conf").read_text().replace(" ", "")
    assert "rtpstart=16384" in rtp and "rtpend=16484" in rtp and "strictrtp=yes" in rtp


def test_only_sipp_rec_calls_are_recorded():
    sipp = EXT[EXT.index("[from-sipp]"):].split("\n[")[0]
    assert "MixMonitor(" in sipp and "exten => _recX.,1" in sipp
    for ctx in ("[from-telnyx]", "[receptionist]"):
        assert "MixMonitor" not in EXT[EXT.index(ctx):].split("\n[")[0]
