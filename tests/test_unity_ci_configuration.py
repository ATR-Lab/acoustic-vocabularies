"""Exercise the actual hosted configuration gate without handling license bytes."""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/unity-foundation.yml"


def gate(**changes):
    section = (
        WORKFLOW.read_text().split("  configuration:\n", 1)[1].split("  quest-and-tests:\n", 1)[0]
    )
    script = textwrap.dedent(section.split("        run: |\n", 1)[1])
    environment = dict(
        os.environ,
        TRUSTED_SOURCE="true",
        RUNNER_READY="true",
        EDITOR_PATH=r"C:\ci\Unity.exe",
        G1_DESCRIPTION=r"C:\ci\g1_description.json",
        LICENSE_MODE="manual_file",
        LICENSE_FILE_PRESENT="true",
    )
    environment.update(changes)
    return subprocess.run(
        ["bash", "-e", "-c", script],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


@pytest.mark.parametrize("key", ["TRUSTED_SOURCE", "RUNNER_READY", "EDITOR_PATH", "G1_DESCRIPTION"])
def test_required_configuration_cannot_be_omitted(key):
    assert gate(**{key: ""}).returncode != 0


@pytest.mark.parametrize("key", ["TRUSTED_SOURCE", "RUNNER_READY"])
def test_false_readiness_or_fork_cannot_be_admitted(key):
    assert gate(**{key: "false"}).returncode != 0


@pytest.mark.parametrize("presence", ["", "false", "1"])
def test_manual_file_requires_secret_presence_boolean(presence):
    assert gate(LICENSE_FILE_PRESENT=presence).returncode != 0


def test_manual_file_accepts_configuration_and_still_requires_actual_probe():
    result = gate()
    assert result.returncode == 0, result.stdout
    assert "actual Windows license/toolchain preflight is required" in result.stdout


def test_hub_activated_account_requires_actual_probe_without_exported_secret():
    result = gate(LICENSE_MODE="preactivated", LICENSE_FILE_PRESENT="false")
    assert result.returncode == 0, result.stdout
    assert "actual Windows license/toolchain preflight is required" in result.stdout


@pytest.mark.parametrize("mode", ["", "personal", "skip", "PREACTIVATED"])
def test_unknown_activation_modes_cannot_bypass_gate(mode):
    assert gate(LICENSE_MODE=mode).returncode != 0


def test_both_modes_require_preflight_before_fixtures_and_builds():
    job = WORKFLOW.read_text().split("  quest-and-tests:\n", 1)[1]
    activation = job.index("      - name: Activate using CI secret\n")
    probe = job.index("      - name: Verify actual licensed Windows runner and immutable cache\n")
    fixtures = job.index("      - name: Prepare sealed synthetic producer fixtures")
    build = job.index("      - name: Configure, test, and build the participant Quest player\n")
    assert activation < probe < fixtures < build
    probe_step = job[probe:fixtures]
    assert "        if:" not in probe_step
    assert "-ProbeLicensedEditor" in probe_step
    assert "-IsolationConfirmed" in probe_step
    assert "-LicenseMode $env:UNITY_LICENSE_MODE" in probe_step
    assert "-RegisterRunner" not in probe_step


def test_hosted_configuration_never_receives_license_contents():
    section = (
        WORKFLOW.read_text().split("  configuration:\n", 1)[1].split("  quest-and-tests:\n", 1)[0]
    )
    assert "LICENSE_FILE_PRESENT: ${{ secrets.UNITY_LICENSE != '' }}" in section
    assert "UNITY_LICENSE: ${{ secrets.UNITY_LICENSE }}" not in section
