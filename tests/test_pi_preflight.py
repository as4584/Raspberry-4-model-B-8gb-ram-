"""S1 RED/GREEN gate for scripts/pi_preflight.sh.

These tests inject fake hardware files and fake commands so the preflight's
*rejection* paths are proven on any host. A pass here is LAPTOP/MOCK evidence
only; it never substitutes for running the script on the physical Pi 4.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pi_preflight.sh"
BASH = shutil.which("bash")
PI4 = "Raspberry Pi 4 Model B Rev 1.5"
MEM_8GB_KB = 7998456

pytestmark = pytest.mark.skipif(BASH is None, reason="bash required for shell preflight")


def _stub(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    # POSIX sandbox PATH has no bash, so use an absolute shebang there.
    # Git Bash's core dir does contain bash (its path has spaces).
    shebang = "/usr/bin/env bash" if os.name == "nt" else BASH
    p.write_text(f"#!{shebang}\n{body}\n", newline="\n")
    p.chmod(0o755)


CORE_UTILS = ("awk", "tr", "date")
OPTIONAL_STUBS = ("git", "tailscale")  # tests may omit these


def _core_utils_dir(tmp_path: Path) -> Path:
    """A PATH dir exposing only core utils, so real git/tailscale can't leak in."""
    if os.name != "nt":
        core = tmp_path / "core"
        core.mkdir()
        for name in CORE_UTILS:
            (core / name).symlink_to(shutil.which(name))
        return core
    # Git Bash: msys tools can't be copied/symlinked away from their DLLs.
    core = Path(shutil.which("awk")).parent
    leaked = [n for n in OPTIONAL_STUBS if (core / n).exists() or (core / f"{n}.exe").exists()]
    assert not leaked, f"real {leaked} in {core} would defeat fixture isolation"
    return core


def run_preflight(tmp_path, *, arch="aarch64", model=PI4, mem_kb=MEM_8GB_KB,
                  py_ok=True, tailscale="connected", git=True):
    root = tmp_path / "root"
    (root / "proc" / "device-tree").mkdir(parents=True)
    if model is not None:
        (root / "proc" / "device-tree" / "model").write_bytes(model.encode() + b"\x00")
    (root / "proc" / "meminfo").write_text(f"MemTotal:       {mem_kb} kB\n", newline="\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "uname", f'echo "{arch}"')
    _stub(bindir, "python3", "exit 0" if py_ok else "exit 1")
    if git:
        _stub(bindir, "git", 'echo "0000000000000000000000000000000000000000"')
    if tailscale == "connected":
        _stub(bindir, "tailscale", 'echo "100.64.0.1"')
    elif tailscale == "logged_out":
        _stub(bindir, "tailscale", "exit 1")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{_core_utils_dir(tmp_path)}",
           "PREFLIGHT_ROOT": str(root)}
    return subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True,
                          text=True, cwd=ROOT, timeout=30)


def test_genuine_pi4_8gb_profile_passes(tmp_path):
    r = run_preflight(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "PI4_PREFLIGHT_OK" in r.stdout
    assert "evidence_kind=FIXTURE" in r.stdout


@pytest.mark.parametrize("kwargs,code", [
    ({"arch": "x86_64"}, 10),
    ({"arch": "armv7l"}, 10),
    ({"model": None}, 11),
    ({"model": "Raspberry Pi 5 Model B Rev 1.0"}, 12),
    ({"model": "Raspberry Pi 3 Model B Plus Rev 1.3"}, 12),
    ({"mem_kb": 3884376}, 13),
    ({"mem_kb": "garbage"}, 13),
    ({"py_ok": False}, 18),
    ({"git": False}, 15),
    ({"tailscale": "missing"}, 16),
    ({"tailscale": "logged_out"}, 17),
])
def test_wrong_environment_is_rejected_with_distinct_code(tmp_path, kwargs, code):
    r = run_preflight(tmp_path, **kwargs)
    assert r.returncode == code, (r.returncode, r.stdout, r.stderr)
    assert "PI4_PREFLIGHT_OK" not in r.stdout
    assert "FAIL:" in r.stderr

