"""Locate the data files published next to the package (JSON Schemas, R scripts).

They live in the source tree (``analysis/schema/``, ``analysis/r/``), which is the single
published copy. The package is used from this repository through an editable or path
dependency; a standalone wheel is not supported.
"""

from __future__ import annotations

from pathlib import Path

_ANALYSIS_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """The ``analysis/`` directory, which contains ``schema/`` and ``r/``."""
    if not (_ANALYSIS_ROOT / "schema").is_dir():
        raise FileNotFoundError(
            f"analysis data files not found under {_ANALYSIS_ROOT}; install av-analysis "
            "from the repository (editable or path dependency)"
        )
    return _ANALYSIS_ROOT


def schema_dir() -> Path:
    """``analysis/schema`` (published JSON Schemas)."""
    return data_root() / "schema"


def schema_path(name: str) -> Path:
    """Path of a published JSON Schema, e.g. ``schema_path("trials-row.schema.json")``."""
    return schema_dir() / name


def r_dir() -> Path:
    """``analysis/r`` (R scripts and the pinned R environment, ``pins.dcf``)."""
    return data_root() / "r"
