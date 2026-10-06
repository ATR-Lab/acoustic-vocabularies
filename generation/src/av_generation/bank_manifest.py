"""Study B bank manifest contract (#26; consumers: Unity #70, package builder #13, #27, #28).

The bank builder itself lives in the `banks/` project (`av_banks`, created by #26), which
depends on this package for the B-mode prompt builder, the ledger and the LLM client. The
manifest format is fixed here: `generation/schema/bank-manifest.schema.json`
(`av-banks/bank-manifest`, version 1). Implemented now: schema validation and the bank
hash; #26 adds the typed reader/writer and the conversion to
`av_sound.dyad_bank.DyadBank` (the provisional package-builder input it replaces).

Bank IDs come from the schedules dyad list (#31): pilot `bank-P001`.., confirmatory
`bank-C001`.. (spares continue the numbering), synthetic `DEMO-...`. Confirmatory tooling
refuses pilot (`bank-P`) and DEMO banks.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from av_generation._schemas import schema_errors
from av_generation.jsonio import canonical_sha256

BANK_MANIFEST_FORMAT: Final = "av-banks/bank-manifest"
BANK_MANIFEST_VERSION: Final = 1
SCHEMA: Final = "bank-manifest.schema.json"
PILOT_PREFIX: Final = "bank-P"
CONFIRMATORY_PREFIX: Final = "bank-C"


def bank_manifest_errors(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Schema errors of a bank manifest (empty when valid)."""
    return schema_errors(SCHEMA, dict(manifest))


def bank_sha256(manifest: Mapping[str, Any]) -> str:
    """The bank hash: SHA-256 of the compact canonical JSON of the whole manifest."""
    return canonical_sha256(dict(manifest))
