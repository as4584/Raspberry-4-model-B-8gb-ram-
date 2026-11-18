"""Mandatory invariant: no secrets committed. Scans every git-tracked file.

Twilio/Tailscale credentials live in Doppler (project pi-receptionist) and are
injected at runtime with `doppler run`; nothing here may contain real values.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "twilio_account_sid": re.compile(r"\bAC[0-9a-f]{32}\b"),
    "twilio_api_key_sid": re.compile(r"\bSK[0-9a-f]{32}\b"),
    "tailscale_key": re.compile(r"\btskey-(auth|api|client)-[A-Za-z0-9]{6,}-[A-Za-z0-9]{10,}"),
    "doppler_token": re.compile(r"\bdp\.(st|sa|ct|pt|scim|audit)\.[A-Za-z0-9_.-]{20,}"),
    # Header plus real key material; a bare header in a negative-test fixture is fine.
    "private_key": re.compile(r"-----BEGIN (OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----\s*[A-Za-z0-9+/=\s]{64,}"),
    "assigned_twilio_token": re.compile(r"TWILIO_AUTH_TOKEN\s*=\s*['\"]?[0-9a-f]{32}"),
}
# Fixture-only values that are deliberately fake.
ALLOW = {"tskey-auth-FAKE0000000000-FIXTURE"}


def tracked_files():
    git = shutil.which("git")
    out = subprocess.run([git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode().split("\0") if p]


def test_no_secret_patterns_in_tracked_files():
    hits = []
    for path in tracked_files():
        try:
            text = path.read_text(errors="ignore")
        except (IsADirectoryError, FileNotFoundError):
            continue
        for name, rx in PATTERNS.items():
            for m in rx.finditer(text):
                if m.group(0) not in ALLOW and not any(a.startswith(m.group(0)) for a in ALLOW):
                    hits.append(f"{path.relative_to(ROOT)}: {name}")
    assert not hits, hits


@pytest.mark.parametrize("name,sample", [
    ("twilio_account_sid", "AC" + "0123456789abcdef" * 2),
    # Samples are concatenated so this file never contains a matching literal.
    ("tailscale_key", "tskey-" + "auth-kAbCdE1CNTRL-AbCdEfGhIjKlMnOp"),
    ("doppler_token", "dp.st.prd." + "x" * 40),
    ("private_key", "-----BEGIN OPENSSH PRIVATE KEY-----\n" + "b3BlbnNzaC1rZXktdjEAAAAA" * 4),
    ("assigned_twilio_token", "TWILIO_AUTH_TOKEN=" + "f" * 32),
])
def test_detector_catches_realistic_secrets(name, sample):
    assert PATTERNS[name].search(sample)


def test_env_example_declares_twilio_names_without_values():
    env = (ROOT / ".env.example").read_text()
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"):
        assert re.search(rf"^{var}=\s*$", env, re.M), var


def test_env_files_are_gitignored():
    git = shutil.which("git")
    r = subprocess.run([git, "check-ignore", "-q", ".env"], cwd=ROOT)
    assert r.returncode == 0
