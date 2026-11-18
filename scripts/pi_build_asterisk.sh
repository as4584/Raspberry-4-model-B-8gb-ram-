#!/usr/bin/env bash
# Build Asterisk from pinned, checksum-verified source (Debian trixie ships no package).
# chan_websocket (22.5+) carries call audio to the Python pipeline; ARI + Stasis control
# the call; PJSIP talks to the SIP provider (Telnyx) and to SIPp in tests.
#   bash scripts/pi_build_asterisk.sh          # run on the Pi as your normal user
set -euo pipefail
dry="${DRY_RUN:-0}"
uid="${BOOTSTRAP_UID:-$(id -u)}"
ver=22.12.0
sha=f8b2ba98aac6c4ff3376bc0a2868bea6ed577ca684efb7ec739ae6e8ce6e35e2
src="${ASTERISK_SRC:-$HOME/opt/asterisk-src}"
url=https://downloads.asterisk.org/pub/telephony/asterisk/releases/asterisk-$ver.tar.gz

[[ "$uid" != "0" ]] || { echo "FAIL: run as your normal user (sudo is used per step)" >&2; exit 61; }
step() { echo "PLAN: $1"; [[ "$dry" == "1" ]] || bash -c "$1"; }

step "sudo apt-get install -y -q build-essential libedit-dev uuid-dev libjansson-dev libxml2-dev libsqlite3-dev libssl-dev libsrtp2-dev pkg-config"
step "mkdir -p $src && cd $src && ([ -f asterisk-$ver.tar.gz ] || curl -fsSLo asterisk-$ver.tar.gz $url)"
step "cd $src && echo '$sha  asterisk-$ver.tar.gz' | sha256sum -c -"
step "cd $src && rm -rf asterisk-$ver && tar xzf asterisk-$ver.tar.gz"
step "cd $src/asterisk-$ver && ./configure --with-jansson --with-pjproject-bundled >/dev/null"
step "cd $src/asterisk-$ver && make menuselect.makeopts >/dev/null && menuselect/menuselect --enable chan_websocket --enable res_ari --enable res_stasis --enable res_pjsip --enable codec_ulaw --enable res_http_websocket menuselect.makeopts"
step "cd $src/asterisk-$ver && make -j4 >/dev/null"
step "cd $src/asterisk-$ver && sudo make install >/dev/null"
step "id asterisk >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin asterisk"
step "sudo install -d -o asterisk -g asterisk /var/lib/asterisk /var/log/asterisk /var/spool/asterisk /var/run/asterisk"
step "/usr/sbin/asterisk -V"
echo "ASTERISK_BUILT: $ver"
