"""Strict JSON parsing and cached JSON Schema validators for the published schemas.

Every schema in `sound/schema/` is registered under its `$id`, so one schema can
refer to another (for example the reserved registry refers to the recipe schema).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from av_sound._paths import data_root, schema_path
from av_sound.recipe import StrictJsonError, strict_json_loads


def _discover_schema_files() -> tuple[str, ...]:
    """Every published schema in `sound/schema/`, sorted by file name.

    Discovered rather than listed, so publishing a new schema changes no code (and
    therefore no `validator_code_hash()` or store chain head).
    """
    return tuple(sorted(p.name for p in (data_root() / "schema").glob("*.schema.json")))


SCHEMA_FILES: tuple[str, ...] = _discover_schema_files()

strict_loads = strict_json_loads
"""Strict JSON with the same rules as `Recipe.from_json` (code `E_JSON`)."""

__all__ = [
    "SCHEMA_FILES",
    "StrictJsonError",
    "load_json_file",
    "load_schema",
    "schema_validator",
    "strict_loads",
]


def load_json_file(path: Path) -> object:
    """Read a UTF-8 JSON file strictly."""
    return strict_loads(path.read_bytes())


@cache
def load_schema(name: str) -> dict[str, Any]:
    """The decoded JSON Schema `sound/schema/<name>`."""
    schema = load_json_file(schema_path(name))
    if not isinstance(schema, dict):
        raise ValueError(f"{name}: a JSON Schema must be an object")
    return schema


@cache
def _registry() -> Registry[Any]:
    resources = [
        (load_schema(name)["$id"], Resource.from_contents(load_schema(name), DRAFT202012))
        for name in SCHEMA_FILES
    ]
    return Registry().with_resources(resources)


@cache
def schema_validator(name: str) -> Draft202012Validator:
    """A cached Draft 2020-12 validator for `sound/schema/<name>`."""
    return Draft202012Validator(load_schema(name), registry=_registry())
