"""T2: Asterisk built from pinned, checksum-verified source on the Pi (Debian trixie
ships no asterisk package). Needs chan_websocket (22.5+), ARI and PJSIP."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pi_build_asterisk.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash required")


def plan(*args, uid="1000"):
    env = {**os.environ, "DRY_RUN": "1", "BOOTSTRAP_UID": uid}
    r = subprocess.run([BASH, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)
    return r, "\n".join(l[6:] for l in r.stdout.splitlines() if l.startswith("PLAN: "))


def test_pins_version_and_verifies_checksum_before_extracting():
    r, p = plan()
    assert r.returncode == 0, r.stderr
    assert "asterisk-22.12.0.tar.gz" in p
    assert "f8b2ba98aac6c4ff3376bc0a2868bea6ed577ca684efb7ec739ae6e8ce6e35e2" in p
    assert p.index("sha256sum -c") < p.index("tar xzf")


def test_enables_websocket_ari_pjsip_and_runs_as_unprivileged_user():
    _, p = plan()
    for mod in ("chan_websocket", "res_ari", "res_pjsip", "res_stasis", "codec_ulaw"):
        assert f"--enable {mod}" in p, mod
    assert "useradd --system" in p and "asterisk" in p
    assert "make -j4" in p and "make install" in p
    assert "make samples" not in p          # we ship our own minimal config, not the samples


def test_refuses_root():
    r, _ = plan(uid="0")
    assert r.returncode == 61
