"""R bridge for the supporting GLMMs (#34): pinned R environment and the call contract.

Policy (``docs/architecture.md``, "R"): R is used only for ``lme4::glmer`` fits with the
fallback ladder. Python stays the orchestrator: it writes the model data and a request
JSON into a temporary folder, runs ``Rscript --vanilla analysis/r/<script>.R
<request.json>``, and reads ``result.json``; no rpy2, no R state between calls, no
network. Versions are pinned in ``analysis/r/pins.dcf`` (R version, a dated Posit Package
Manager CRAN snapshot, package versions); ``analysis/r/install.R`` installs them and
``analysis/r/check_pins.R`` verifies them. Tests that need R are marked ``needs_r``; they
skip when ``Rscript`` is missing and run in CI (``analysis.yml``, job ``r``).

Implemented here: :func:`r_pins`, :func:`find_rscript` (skeleton) and :func:`run_r` (#34).
Every R script called through :func:`run_r` writes ``result.json`` with a ``versions``
object (``R`` and every pinned package, as ``packageDescription()`` reports them);
:func:`run_r` refuses a result whose versions differ from the pins, so a GLMM is never
fitted with an unpinned lme4.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ._paths import r_dir
from .fileio import json_bytes

PINS_FILE: Final = "pins.dcf"
RSCRIPT_ENV: Final = "AV_RSCRIPT"  # optional path to Rscript (else PATH)
REQUEST_FILE: Final = "request.json"
RESULT_FILE: Final = "result.json"
_TAIL: Final = 2000  # characters of R output quoted in errors


@dataclass(frozen=True)
class RPins:
    """The pinned R environment."""

    r_version: str
    snapshot: str  # Posit Package Manager CRAN snapshot date (YYYY-MM-DD)
    packages: Mapping[str, str]  # package -> exact version


def parse_dcf(text: str) -> dict[str, str]:
    """Fields of a one-record Debian control file (``Key: value`` lines, ``#`` comments)."""
    fields: dict[str, str] = {}
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or not value.strip():
            raise ValueError(f"line {n}: expected 'Key: value'")
        if key.strip() in fields:
            raise ValueError(f"line {n}: duplicate key {key.strip()!r}")
        fields[key.strip()] = value.strip()
    return fields


def r_pins(path: Path | None = None) -> RPins:
    """Read ``analysis/r/pins.dcf``: fields ``R``, ``Snapshot`` and one per package."""
    path = r_dir() / PINS_FILE if path is None else path
    fields = parse_dcf(path.read_text(encoding="utf-8"))
    try:
        r_version = fields.pop("R")
        snapshot = fields.pop("Snapshot")
    except KeyError as exc:
        raise ValueError(f"{path}: missing field {exc}") from None
    return RPins(r_version, snapshot, dict(sorted(fields.items())))


def find_rscript() -> Path | None:
    """``Rscript`` from ``$AV_RSCRIPT`` or ``PATH``, or None when R is not installed."""
    configured = os.environ.get(RSCRIPT_ENV)
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    found = shutil.which("Rscript")
    return None if found is None else Path(found)


def _norm_version(text: str) -> str:
    """``2.0-6`` and ``2.0.6`` compare equal (R's package_version)."""
    return text.strip().replace("-", ".")


def version_problems(reported: Mapping[str, Any], pins: RPins | None = None) -> list[str]:
    """Differences between the versions an R script reported and the pins."""
    pins = r_pins() if pins is None else pins
    expected = {"R": pins.r_version, **pins.packages}
    problems = []
    for name, version in expected.items():
        got = reported.get(name)
        if not isinstance(got, str) or _norm_version(got) != _norm_version(version):
            problems.append(f"{name}: pinned {version}, R reported {got!r}")
    return problems


def run_r(
    script: str,
    request: Mapping[str, Any],
    files: Mapping[str, bytes],
    *,
    timeout_s: float = 600.0,
) -> dict[str, Any]:
    """Run ``analysis/r/<script>`` on ``request`` (written as ``request.json``) and
    ``files`` (written next to it) in a fresh temporary folder; return ``result.json``.

    Raises ``RuntimeError`` when R is missing, exits non-zero, times out, or reports
    package versions that differ from :func:`r_pins`.
    """
    rscript = find_rscript()
    if rscript is None:
        raise RuntimeError(f"Rscript not found (install the pinned R or set {RSCRIPT_ENV})")
    path = r_dir() / script
    if not path.is_file() or "/" in script or "\\" in script:
        raise RuntimeError(f"unknown R script {script!r}")
    for name in files:
        if name in (REQUEST_FILE, RESULT_FILE) or "/" in name or "\\" in name or not name:
            raise ValueError(f"invalid file name {name!r}")
    with tempfile.TemporaryDirectory(prefix="av-r-") as tmp:
        folder = Path(tmp)
        (folder / REQUEST_FILE).write_bytes(json_bytes(dict(request)))
        for name, data in sorted(files.items()):
            (folder / name).write_bytes(data)
        try:
            proc = subprocess.run(
                [str(rscript), "--vanilla", str(path), str(folder / REQUEST_FILE)],
                cwd=folder,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"R script {script} timed out after {timeout_s:g} s") from None
        except OSError as exc:
            raise RuntimeError(f"cannot run {rscript}: {exc}") from None
        output = (proc.stdout + proc.stderr)[-_TAIL:]
        if proc.returncode != 0:
            raise RuntimeError(f"R script {script} exited {proc.returncode}: {output}")
        result_path = folder / RESULT_FILE
        if not result_path.is_file():
            raise RuntimeError(f"R script {script} wrote no {RESULT_FILE}: {output}")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except ValueError:
            raise RuntimeError(f"R script {script}: {RESULT_FILE} is not JSON") from None
    if not isinstance(result, dict) or not isinstance(result.get("versions"), dict):
        raise RuntimeError(f"R script {script}: result without a versions object")
    problems = version_problems(result["versions"])
    if problems:
        raise RuntimeError("R environment differs from analysis/r/pins.dcf: " + "; ".join(problems))
    return result
