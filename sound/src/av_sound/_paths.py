"""Locate the data files published next to the package (schemas, registries).

They live in the source tree (`sound/schema/`, `sound/reserved/`), which is the
single published copy. The package is used from this repository through an
editable or path dependency; a standalone wheel is not supported.
"""

from __future__ import annotations

from pathlib import Path

_SOUND_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """The `sound/` directory, which contains `schema/` and `reserved/`."""
    if not (_SOUND_ROOT / "schema").is_dir():
        raise FileNotFoundError(
            f"sound data files not found under {_SOUND_ROOT}; install av-sound from the "
            "repository (editable or path dependency)"
        )
    return _SOUND_ROOT


def schema_path(name: str) -> Path:
    """Path of a published JSON Schema, e.g. `schema_path("recipe.schema.json")`."""
    return data_root() / "schema" / name
