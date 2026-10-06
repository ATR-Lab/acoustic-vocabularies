"""G4 freeze manifest (#25). INTERFACE ONLY in the skeleton; the document format is
`generation/schema/freeze-manifest.schema.json` (`av-generation/freeze-manifest`).

The manifest lists every frozen value with its SHA-256 (when it is a file or a document)
and its source, is readable without the code, and is committed and tagged
(proposed `generation/FREEZE-v1.0.json`). Sign-off records roles and links only, never
names. After G4 a CI freeze guard fails when any current value differs, and confirmatory
runs (#28, O7.1.1) refuse to start when their config hash differs.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Final

FREEZE_FORMAT: Final = "av-generation/freeze-manifest"

REQUIRED_ITEM_KEYS: Final[tuple[str, ...]] = (
    "renderer.version",
    "renderer.hash",
    "renderer.recipe_schema_hash",
    "renderer.golden_manifest_sha256",
    "validator.version",
    "validator.hash",
    "separation.threshold",
    "model.id",
    "model.revision",
    "model.tokenizer_revision",
    "model.weights_sha256",
    "model.license",
    "runtime.vllm_version",
    "runtime.cuda_version",
    "runtime.driver_version",
    "runtime.gpu",
    "runtime.precision",
    "runtime.max_model_len",
    "runtime.chat_template_sha256",
    "decoding.temperature",
    "decoding.top_p",
    "decoding.top_k",
    "decoding.repetition_penalty",
    "decoding.max_tokens",
    "decoding.max_input_tokens",
    "decoding.implementation",
    "schema.decoding_sha256",
    "prompts.a3_sha256",
    "prompts.b_sha256",
    "seeds.function",
    "seeds.namespaces",
    "budget.study_a",
    "budget.study_b",
    "a2.rules",
    "selector.rules",
    "fallback.bank_hash",
    "fallback.books_sha256",
    "pilot.timing_review",
)
"""Item keys every freeze manifest must contain (#25 may add keys, never drop one)."""

APPARATUS_FIELDS: Final[tuple[str, ...]] = (
    "renderer_recipe_schema_hash",
    "model_revision",
    "runtime_precision",
    "prompt_hash",
    "fallback_bank_hash",
)
"""Apparatus-manifest fields the freeze manifest fills."""


def build_freeze_manifest(values: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble the manifest document from collected values (#25)."""
    raise NotImplementedError("#25: freeze manifest")


def freeze_differences(
    manifest_path: str | os.PathLike[str], current: Mapping[str, Any]
) -> list[str]:
    """Items whose current value or hash differs from the manifest (the CI freeze guard)."""
    raise NotImplementedError("#25: freeze guard")
