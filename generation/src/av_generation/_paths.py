"""Locate the data files published next to the package (schemas, test vectors, examples).

They live in the source tree (`generation/schema/`, `generation/testvectors/`,
`generation/examples/`), which is the single published copy. The package is used from
this repository through an editable or path dependency; a standalone wheel is not
supported (the same rule as `av_sound._paths`).
"""

from __future__ import annotations

from pathlib import Path

_GENERATION_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """The `generation/` directory, which contains `schema/`, `testvectors/` and `examples/`."""
    if not (_GENERATION_ROOT / "schema").is_dir():
        raise FileNotFoundError(
            f"generation data files not found under {_GENERATION_ROOT}; install av-generation "
            "from the repository (editable or path dependency)"
        )
    return _GENERATION_ROOT


def schema_dir() -> Path:
    """`generation/schema/`."""
    return data_root() / "schema"


def schema_path(name: str) -> Path:
    """Path of a published JSON Schema, e.g. `schema_path("slot-record.schema.json")`."""
    return schema_dir() / name


def testvectors_path(*parts: str) -> Path:
    """Path under `generation/testvectors/`."""
    return data_root().joinpath("testvectors", *parts)


def examples_path(*parts: str) -> Path:
    """Path under `generation/examples/` (synthetic DEMO examples only)."""
    return data_root().joinpath("examples", *parts)
