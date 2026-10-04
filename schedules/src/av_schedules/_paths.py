"""Locate the data files published next to the package (JSON Schemas, DEMO examples).

They live in the source tree (``schedules/schema/``, ``schedules/examples/``), which is
the single published copy. The package is used from this repository through an editable
or path dependency; a standalone wheel is not supported.
"""

from __future__ import annotations

from pathlib import Path

_SCHEDULES_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """The ``schedules/`` directory, which contains ``schema/`` and ``examples/``."""
    if not (_SCHEDULES_ROOT / "schema").is_dir():
        raise FileNotFoundError(
            f"schedules data files not found under {_SCHEDULES_ROOT}; install av-schedules "
            "from the repository (editable or path dependency)"
        )
    return _SCHEDULES_ROOT


def schema_path(name: str) -> Path:
    """Path of a published JSON Schema, e.g. ``schema_path("permutation.schema.json")``."""
    return data_root() / "schema" / name


def default_out_dir() -> Path:
    """``schedules/out`` (git-ignored by ``schedules/.gitignore``)."""
    return data_root() / "out"


def examples_dir() -> Path:
    """``schedules/examples/demo`` (committed DEMO outputs)."""
    return data_root() / "examples" / "demo"
