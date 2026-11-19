"""T2/T5: our own minimal Asterisk config (no samples). Pins the security properties:
loopback-only HTTP/ARI, the media socket on 127.0.0.1, SIPp allowed only from
localhost, Telnyx credentials only as ${ENV} placeholders, and the dialplan wiring."""
import re
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "deploy" / "asterisk"


def conf(name):
    return (D / name).read_text()


def test_http_and_ari_listen_on_loopback_only():
    http = conf("http.conf")
    assert re.search(r"^bindaddr\s*=\s*127\.0\.0\.1$", http, re.M)
    assert "0.0.0.0" not in http and not re.search(r"^tlsenable\s*=\s*yes", http, re.M)
    ari = conf("ari.conf.tmpl")
    assert re.search(r"^enabled\s*=\s*yes", ari, re.M) and "${ARI_PASSWORD}" in ari


def test_media_socket_targets_local_receptionist_with_json_control():
    ws = conf("websocket_client.conf")
    assert "uri = ws://127.0.0.1:8000/asterisk/media" in ws and "protocols = media" in ws
    assert re.search(r"connection_type\s*=\s*per_call_config", ws)
    assert re.search(r"^control_message_format\s*=\s*json$", conf("chan_websocket.conf"), re.M)


def test_dialplan_answers_and_dials_websocket_with_caller_number():
    ext = conf("extensions.conf")
    assert "Dial(WebSocket/receptionist/c(ulaw)v(caller=${URIENCODE(${CALLERID(num)})}," in ext
    for ctx in ("[from-telnyx]", "[from-sipp]", "[receptionist]"):
        assert ctx in ext
    assert "Answer()" in ext


def test_sipp_endpoint_is_localhost_only_and_telnyx_uses_env_placeholders():
    pj = conf("pjsip.conf.tmpl")
    sipp = pj[pj.index("[sipp-identify]"):]
    assert "match = 127.0.0.1" in sipp.split("\n[")[0]
    for var in ("${TELNYX_SIP_USERNAME}", "${TELNYX_SIP_PASSWORD}"):
        assert var in pj
    assert not re.search(r"password\s*=\s*[^$\s]", pj)          # no literal password


def test_no_secrets_or_public_bind_anywhere():
    for f in D.iterdir():
        text = f.read_text()
        assert not re.search(r"(secret|password)\s*=\s*(?!\$\{)\S", text, re.I), f.name


def test_modules_autoload_with_explicit_noloads():
    # Pi 2026-11-18: an autoload=no allow-list missed transitive deps (res_ari_model,
    # res_stasis_*), so chan_pjsip declined to load and SIPp got only "100 Trying".
    mods = conf("modules.conf")
    assert re.search(r"^autoload\s*=\s*yes$", mods, re.M)
    assert not re.search(r"^noload\s*=\s*(chan_pjsip|chan_websocket|res_pjsip|res_ari|res_stasis)\.so$", mods, re.M)
    for unwanted in ("chan_iax2.so", "res_pjsip_phoneprov.so", "app_voicemail.so"):
        assert f"noload = {unwanted}" in mods
