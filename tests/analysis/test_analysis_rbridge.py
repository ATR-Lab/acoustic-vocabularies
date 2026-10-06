"""Pinned R environment (analysis/r/pins.dcf) and the R CI job."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from av_analysis.rbridge import find_rscript, parse_dcf, r_pins

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "analysis.yml"


def test_pins_are_exact_versions():
    pins = r_pins()
    assert re.fullmatch(r"4\.[0-9]+\.[0-9]+", pins.r_version)
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", pins.snapshot)
    assert {"lme4", "Matrix", "jsonlite"} <= set(pins.packages)
    for version in pins.packages.values():
        assert re.fullmatch(r"[0-9]+(\.[0-9]+)*(-[0-9]+)?", version)


def test_ci_installs_the_pinned_r_version():
    text = WORKFLOW.read_text(encoding="utf-8")
    versions = re.findall(r"r-version:\s*['\"]?([0-9.]+)", text)
    assert versions == [r_pins().r_version]
    assert "analysis/r/install.R" in text and "-m needs_r" in text


def test_parse_dcf():
    assert parse_dcf("# c\nR: 4.6.1\n\nlme4: 2.0-6\n") == {"R": "4.6.1", "lme4": "2.0-6"}
    for bad in ("R 4.6.1\n", "R:\n", "R: 1\nR: 2\n"):
        with pytest.raises(ValueError):
            parse_dcf(bad)


def test_r_pins_requires_r_and_snapshot(tmp_path):
    path = tmp_path / "pins.dcf"
    path.write_text("lme4: 2.0-6\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing field"):
        r_pins(path)


def test_find_rscript_honours_the_environment(tmp_path, monkeypatch):
    fake = tmp_path / "Rscript"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("AV_RSCRIPT", str(fake))
    assert find_rscript() == fake
    monkeypatch.setenv("AV_RSCRIPT", str(tmp_path / "missing"))
    assert find_rscript() is None
    monkeypatch.delenv("AV_RSCRIPT")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert find_rscript() is None


@pytest.mark.needs_r
def test_installed_r_packages_match_the_pins():
    rscript = find_rscript()
    assert rscript is not None
    result = subprocess.run(
        [str(rscript), "--vanilla", str(ROOT / "analysis" / "r" / "check_pins.R")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count(" ok") == 1 + len(r_pins().packages)
