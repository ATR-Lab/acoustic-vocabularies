"""Strict JSON parsing and cached JSON Schema validators for the published schemas.

Every schema in `sound/schema/` is registered under its `$id`, so one schema can
refer to another (for example the reserved registry refers to the recipe schema).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, NoReturn

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from av_sound._paths import schema_path

SCHEMA_FILES: tuple[str, ...] = (
    "recipe.schema.json",
    "reserved-registry.schema.json",
    "validation-result.schema.json",
    "validator-config.schema.json",
)


class StrictJsonError(ValueError):
    """Text that is not strict JSON: syntax errors, NaN/Infinity, duplicate keys, bad UTF-8."""


def _reject_constant(name: str) -> NoReturn:
    raise StrictJsonError(f"{name} is not valid JSON")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise StrictJsonError(f"duplicate key {key!r}")
        out[key] = value
    return out


def strict_loads(text: str | bytes | bytearray) -> object:
    """Parse JSON text. Bytes must be UTF-8. Raises `StrictJsonError` on any violation."""
    try:
        if isinstance(text, bytes | bytearray):
            text = bytes(text).decode("utf-8")
        return json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_reject_constant)
    except StrictJsonError:
        raise
    except (ValueError, RecursionError) as exc:  # JSONDecodeError, UnicodeDecodeError
        raise StrictJsonError(str(exc)) from exc


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
