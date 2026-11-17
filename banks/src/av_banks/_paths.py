"""Locate the data files published next to the package (`banks/schema/`) and the
sample-size presets of the schedules stack (`schedules/presets/`).

They live in the source tree, which is the single published copy. The package is used
from this repository through an editable or path dependency; a standalone wheel is not
supported (the same rule as `av_sound._paths` and `av_generation._paths`).
"""

from __future__ import annotations

from pathlib import Path

_BANKS_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """The `banks/` directory, which contains `schema/`."""
    if not (_BANKS_ROOT / "schema").is_dir():
        raise FileNotFoundError(
            f"banks data files not found under {_BANKS_ROOT}; install av-banks from the "
            "repository (editable or path dependency)"
        )
    return _BANKS_ROOT


def schema_path(name: str) -> Path:
    """Path of a published banks JSON Schema, e.g. `schema_path("bank-attempt.schema.json")`."""
    return data_root() / "schema" / name


def sample_size_presets_path() -> Path:
    """`schedules/presets/sample-size-presets.json` of the same checkout (#266): the single
    source of the confirmatory unit counts. A file contract: the schedules package is not
    imported."""
    path = _BANKS_ROOT.parent / "schedules" / "presets" / "sample-size-presets.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"sample-size presets not found at {path.parent.parent.name}/presets/"
            f"{path.name}; use av-banks from the repository checkout"
        )
    return path
