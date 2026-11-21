#!/usr/bin/env bash
# Run ON THE PI:  sudo bash scripts/pi_verify.sh
# Adapted from the owner's kit verify.sh: same checks, our paths; the kit's Stasis loopback
# (our pipeline uses chan_websocket, not Stasis) is replaced by a real SIPp booking call.
# Prints PASS/FAIL only, never secret values.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"; fail=0
ok(){ echo "PASS  $*"; }; bad(){ echo "FAIL  $*"; fail=1; }
ast(){ asterisk -rx "$1" 2>/dev/null; }

# 1. Secrets plumbing
systemctl is-active -q pi-receptionist-secrets && ok "secrets rendered from Doppler" || bad "pi-receptionist-secrets.service"
findmnt -n -o FSTYPE --target /run/asterisk-secrets | grep -q tmpfs && ok "secrets on tmpfs (RAM only)" || bad "secrets not on tmpfs"
[[ "$(stat -c %a /run/asterisk-secrets/pjsip-secrets.conf 2>/dev/null)" == 640 ]] && ok "secret file mode 640" || bad "secret file perms"
if grep -rqiE 'password *= *[^$ ]' /etc/asterisk/ 2>/dev/null; then bad "plaintext password in /etc/asterisk"; else ok "no plaintext passwords in /etc/asterisk"; fi
( set -a; . /etc/pi-receptionist/doppler.env; set +a; cd /tmp; export HOME=/var/lib/pi-receptionist/secrets
  if doppler secrets set __probe=x --silent >/dev/null 2>&1; then
    echo "FAIL  token can WRITE (should be read-only)"; doppler secrets delete __probe -y --silent >/dev/null 2>&1; exit 1
  else echo "PASS  service token is read-only"; fi
  if doppler secrets --config dev --only-names --silent >/dev/null 2>&1; then echo "FAIL  token can read dev (should be prd-only)"; exit 1
  else echo "PASS  token scoped to prd only"; fi ) || fail=1

# 2. Network exposure
ss -ltnH 'sport = :8088' | grep -q '127.0.0.1:8088' && ok "ARI bound to 127.0.0.1" || bad "ARI not loopback-only"
ss -ltnH 'sport = :8088' | grep -vq '127.0.0.1' && bad "ARI listening on non-loopback" || true
ss -ltnH 'sport = :8000' | grep -vq '127.0.0.1' && bad "app listening on non-loopback" || ok "app bound to 127.0.0.1"
ufw status | grep -qE '^22/tcp on tailscale0' && ok "SSH only via Tailscale" || bad "SSH firewall rule"
tailscale status >/dev/null 2>&1 && ok "Tailscale up" || bad "Tailscale down"

# 3. Telnyx TLS registration
ast "pjsip show transports" | grep -q 'transport-tls.*tls' && ok "TLS transport loaded" || bad "TLS transport"
ast "pjsip show registrations" | grep -q 'telnyx-reg.*Registered' && ok "registered to Telnyx over TLS" || bad "not registered"
ast "pjsip show endpoint telnyx" | grep -q 'media_encryption *: *sdes' && ok "SRTP (SDES) required on telnyx endpoint" || bad "SRTP not set"

# 4. SIPp loopback tests (127.0.0.1:5070)
if command -v sipp >/dev/null; then
  ( cd /tmp && sipp 127.0.0.1:5070 -sf "$HERE/tests/sipp/uac_echo.xml" -s echo -i 127.0.0.1 -p 5071 -m 3 -l 1 -r 1 \
      -timeout 30s -nostdin >/dev/null 2>&1 ) && ok "SIPp echo: 3/3 calls completed" || bad "SIPp echo"
  out="$(cd "$HERE" && sudo -u prototype env PIPER_CLI=/home/prototype/opt/piper/piper \
        PIPER_MODEL=/home/prototype/models/piper/en_US-lessac-medium.onnx \
        .venv/bin/python scripts/sipp_call_test.py --calls 1 2>&1)"
  grep -q SIPP_CALL_OK <<<"$out" && ok "SIPp booking call through the AI pipeline" || bad "SIPp booking call"
else bad "sipp not installed (apt install sip-tester)"; fi

# 5. Leak checks
journalctl -u pi-receptionist-secrets -u asterisk -u pi-receptionist --since -1d 2>/dev/null \
  | grep -qiE 'password=|authorization: digest|dp\.st\.' && bad "possible secret in journal" || ok "no secrets in journal"
grep -qiE 'authorization: digest|password=' /var/log/asterisk/* 2>/dev/null && bad "possible secret in asterisk logs" || ok "no secrets in asterisk logs"

echo; [[ $fail == 0 ]] && echo "ALL CHECKS PASSED" || echo "Some checks failed."
exit $fail
