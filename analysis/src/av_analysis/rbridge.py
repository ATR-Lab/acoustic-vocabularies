"""R bridge for the supporting GLMMs (#34): pinned R environment and the call contract.

Policy (``docs/architecture.md``, "R"): R is used only for ``lme4::glmer`` fits with the
fallback ladder. Python stays the orchestrator: it writes the model data and a request
JSON into a temporary folder, runs ``Rscript --vanilla analysis/r/<script>.R
<request.json>``, and reads ``result.json``; no rpy2, no R state between calls, no
network. Versions are pinned in ``analysis/r/pins.dcf`` (R version, a dated Posit Package
Manager CRAN snapshot, package versions); ``analysis/r/install.R`` installs them and
``analysis/r/check_pins.R`` verifies them. Tests that need R are marked ``needs_r``; they
skip when ``Rscript`` is missing and run in CI (``analysis.yml``, job ``r``).

Implemented here: :func:`r_pins`, :func:`find_rscript`. Interface (#34): :func:`run_r`.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ._paths import r_dir

PINS_FILE: Final = "pins.dcf"
RSCRIPT_ENV: Final = "AV_RSCRIPT"  # optional path to Rscript (else PATH)


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
    raise NotImplementedError("#34: R bridge")
