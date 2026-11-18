"""The CI pipeline is itself a regression gate; these tests pin what it must run.
Changing CI to drop a gate makes this file fail first."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
JOBS = CI["jobs"]


def steps_text(job):
    return "\n".join(str(s.get("run", "")) + str(s.get("uses", "")) for s in JOBS[job]["steps"])


def test_runs_on_every_push_and_pr():
    on = CI.get("on", CI.get(True))  # PyYAML parses bare `on` as True
    assert "push" in on and "pull_request" in on


def test_matrix_covers_pi_python():
    """The Pi runs Raspberry Pi OS Trixie = Python 3.13 (measured 3.13.5)."""
    versions = {str(v) for v in JOBS["pytest"]["strategy"]["matrix"]["python-version"]}
    assert {"3.11", "3.13"} <= versions


def test_pytest_job_enforces_coverage_floor():
    s = steps_text("pytest")
    assert "--cov=receptionist" in s and "--cov-fail-under=" in s


def test_shell_scripts_are_linted():
    assert "shellcheck" in JOBS and "shellcheck" in steps_text("shellcheck")


def test_mutation_testing_gates_booking_core():
    job = JOBS["mutation"]
    s = steps_text("mutation")
    assert "mutmut run" in s and "mutation_gate.py" in s
    assert job.get("timeout-minutes", 0) > 0


def test_mutation_targets_safety_critical_modules():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "[tool.mutmut]" in pyproject
    assert "receptionist/booking.py" in pyproject


def test_dependencies_install_from_pyproject_only():
    assert "pip install -e" in steps_text("pytest")
