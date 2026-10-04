"""Renderer version and renderer hash (renderer spec D10).

`renderer_hash()` fingerprints the exact implementation: renderer and spec version,
constants, table digests and code digests of the byte-path modules (Python 3.11 AST
with docstrings removed, so comments and formatting do not change it).
`renderer_recipe_schema_hash()` adds the recipe schema and is the value for the
apparatus manifest field of the same name.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from av_sound import renderer, tables, wav
from av_sound._paths import schema_path
from av_sound.recipe import Profile

SPEC_VERSION = "0.1.0"
"""Version of `sound/docs/renderer-spec.md` that this implementation follows."""

RENDERER_SOURCES: tuple[str, ...] = ("renderer.py", "tables.py", "wav.py")


def _canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def code_digest(path: Path) -> str:
    """SHA-256 of the module's AST dump with docstrings and bare strings removed."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            kept = [
                stmt
                for stmt in body
                if not (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                )
            ]
            node.body = kept or [ast.Pass()]  # type: ignore[attr-defined]
    dump = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()


def renderer_manifest() -> dict[str, Any]:
    """Everything that determines rendered bytes, in a JSON-compatible dict."""
    pkg = Path(renderer.__file__).resolve().parent
    return {
        "renderer_version": renderer.RENDERER_VERSION,
        "spec_version": SPEC_VERSION,
        "constants": {
            "sample_rate": tables.SAMPLE_RATE,
            "sine_bits": tables.SINE_BITS,
            "sine_q": tables.SINE_Q,
            "env_q": tables.ENV_Q,
            "attack_samples": tables.ATTACK_SAMPLES,
            "release_samples": tables.RELEASE_SAMPLES,
            "phase_bits": tables.PHASE_BITS,
            "harmonic_weights_x20": list(renderer.HARMONIC_WEIGHTS_X20),
            "profile_f0_hz": {p.value: p.f0_hz for p in Profile},
            "work_shift": renderer.WORK_SHIFT,
            "gain_frac_bits": renderer.GAIN_FRAC_BITS,
            "rms_target": renderer.RMS_TARGET,
            "full_scale": renderer.FULL_SCALE,
            "min_event_samples": renderer.MIN_EVENT_SAMPLES,
            "bits_per_sample": wav.BITS_PER_SAMPLE,
        },
        "tables": tables.table_digests(),
        "code": {name: code_digest(pkg / name) for name in RENDERER_SOURCES},
    }


def renderer_hash() -> str:
    """SHA-256 of the compact canonical JSON of `renderer_manifest()`."""
    return hashlib.sha256(_canonical(renderer_manifest())).hexdigest()


def recipe_schema_sha256() -> str:
    """SHA-256 of `recipe.schema.json` with CRLF normalized to LF."""
    return _lf_sha256(schema_path("recipe.schema.json"))


def renderer_recipe_schema_hash() -> str:
    """Apparatus-manifest value `renderer_recipe_schema_hash` (spec D10)."""
    return hashlib.sha256(
        _canonical(
            {"recipe_schema_sha256": recipe_schema_sha256(), "renderer_hash": renderer_hash()}
        )
    ).hexdigest()
