"""Cached Draft 2020-12 validators for the generation schemas.

Every schema in `generation/schema/` and in `sound/schema/` is registered under its
`$id`, so a generation schema can refer to a sound schema by absolute `$id` (for example
the recipe or the fallback-scan record) and to `common.schema.json` by relative reference.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from pathlib import Path
from typing import Any

from av_sound._paths import data_root as sound_root
from av_sound.recipe import strict_json_loads
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from av_generation._paths import schema_dir

SCHEMA_ID_PREFIX = "https://github.com/ATR-Lab/acoustic-vocabularies/blob/main/generation/schema/"
"""`$id` of `generation/schema/<name>` is this prefix + `<name>`."""
SOUND_SCHEMA_ID_PREFIX = "https://github.com/ATR-Lab/acoustic-vocabularies/blob/main/sound/schema/"
RECIPE_SCHEMA_ID = SOUND_SCHEMA_ID_PREFIX + "recipe.schema.json"


def schema_files() -> tuple[str, ...]:
    """Every published generation schema file name, sorted."""
    return tuple(sorted(p.name for p in schema_dir().glob("*.schema.json")))


def _load(path: Path) -> dict[str, Any]:
    data = strict_json_loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: a JSON Schema must be an object")
    return data


@cache
def load_schema(name: str) -> dict[str, Any]:
    """The decoded JSON Schema `generation/schema/<name>`."""
    return _load(schema_dir() / name)


def _resources(paths: Iterable[Path]) -> list[tuple[str, Resource[Any]]]:
    out = []
    for path in paths:
        schema = _load(path)
        out.append((str(schema["$id"]), Resource.from_contents(schema, DRAFT202012)))
    return out


@cache
def _registry() -> Registry[Any]:
    sound = sorted((sound_root() / "schema").glob("*.schema.json"))
    own = sorted(schema_dir().glob("*.schema.json"))
    return Registry().with_resources(_resources([*sound, *own]))


@cache
def schema_validator(name: str) -> Draft202012Validator:
    """A cached Draft 2020-12 validator for `generation/schema/<name>`."""
    return Draft202012Validator(load_schema(name), registry=_registry())


def schema_errors(name: str, instance: object) -> tuple[str, ...]:
    """Sorted error messages (`path: message`) of `instance` against schema `name`."""
    errors = []
    for err in schema_validator(name).iter_errors(instance):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{where}: {err.message}")
    return tuple(sorted(errors))
