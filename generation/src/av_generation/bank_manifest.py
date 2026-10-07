"""Study B bank manifest contract (#26; consumers: Unity #70, package builder #13, #27, #28).

The bank builder itself lives in the `banks/` project (`av_banks`, created by #26), which
depends on this package for the B-mode prompt builder, the ledger and the LLM client.
Fixed here and implemented (shared contract): the manifest schema
(`bank-manifest.schema.json`, `av-banks/bank-manifest` v1), the bank hash, the amendment
log (`bank-amendment.schema.json`) and the effective menu after amendments. #26 adds the
typed reader/writer, `verify`, `amend` and the conversion to
`av_sound.dyad_bank.DyadBank` (the provisional package-builder input it replaces).

Bank directory (`rundir.RunLayout.bank_dir(bank_id)`):

```
manifest.json            the built bank (immutable once written)
generation-config.json   the GenerationConfig it was built under (genconfig)
amendments.jsonl         append-only reserve-rule amendments (may be absent)
attempts/<n>/slots.jsonl every attempt's slot records, failed attempts included
options/P<k>/<atom>-<rank>.wav
```

- `bank_id` = `bank-<P|C><seq:03d>` from the dyad-slot sequence (schedules allocation,
  #31: pilot `bank-P001`.., confirmatory `bank-C001`.., spares continue the numbering),
  or `DEMO-...`; the schema ties `set` to the prefix. Labels and the traversal order come
  from the unit's package-safe `<unit>/permutation.json` (`labels`, `atom_order`; its
  SHA-256 in `permutation_sha256`). The builder never reads `<set>-dyads.json` (roles
  and arms stay concealed).
- `seed_namespace` is the `<bank_ns>` part of every B seed key (`seeds.b_seed_key`):
  the bank ID on a first build, a new namespace for every rebuild under a new
  `bank_version`.
- `generation_config_sha256` = `GenerationConfig.frozen_sha256()` of the stored
  `generation-config.json`; confirmatory builds equal the G4 freeze value (#28).
- `bank_sha256(manifest)` covers the whole manifest. Amendments never change it: they
  are appended to `amendments.jsonl`, each line a `bank_amendment` record chained by
  `prev_sha256` (the bank hash for the first, else the previous line's
  `canonical_sha256`) and naming the replaced option, the reserve that replaces it and
  the compatibility recheck. A register hash committed before allocation (#28) stays
  valid; consumers (#70 menus, #13 dyad packages) call `effective_menu(manifest,
  amendments)` after `amendment_chain_errors(...) == ()`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from av_generation._schemas import schema_errors
from av_generation.ids import IdError, bank_set
from av_generation.jsonio import canonical_sha256

BANK_MANIFEST_FORMAT: Final = "av-banks/bank-manifest"
BANK_MANIFEST_VERSION: Final = 1
SCHEMA: Final = "bank-manifest.schema.json"
AMENDMENT_SCHEMA: Final = "bank-amendment.schema.json"
MANIFEST_NAME: Final = "manifest.json"
AMENDMENTS_NAME: Final = "amendments.jsonl"
PILOT_PREFIX: Final = "bank-P"
CONFIRMATORY_PREFIX: Final = "bank-C"


class BankSetError(ValueError):
    """A bank of another set was loaded (e.g. a pilot bank in confirmatory mode)."""


def bank_manifest_errors(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Schema errors of a bank manifest (empty when valid)."""
    return schema_errors(SCHEMA, dict(manifest))


def bank_sha256(manifest: Mapping[str, Any]) -> str:
    """The bank hash: SHA-256 of the compact canonical JSON of the whole manifest."""
    return canonical_sha256(dict(manifest))


def require_bank_set(manifest: Mapping[str, Any], expected: str) -> None:
    """Refuse a bank whose `set` or ID prefix is not `expected` (#27: a confirmatory-mode
    load of a pilot bank fails with a clear error)."""
    bank_id = manifest.get("bank_id")
    try:
        found = bank_set(str(bank_id))
    except IdError as err:
        raise BankSetError(str(err)) from None
    if found != expected or manifest.get("set") != expected:
        raise BankSetError(
            f"bank {bank_id!r} is a {manifest.get('set')} bank; {expected} mode refuses it"
        )


def bank_amendment_errors(entry: Mapping[str, Any]) -> tuple[str, ...]:
    """Schema errors of one amendment record (empty when valid)."""
    return schema_errors(AMENDMENT_SCHEMA, dict(entry))


def _options(manifest: Mapping[str, Any]) -> dict[tuple[str, str], dict[int, str]]:
    return {
        (cell["profile"], cell["atom_id"]): {o["rank"]: o["option_id"] for o in cell["options"]}
        for cell in manifest.get("cells", ())
    }


def amendment_chain_errors(
    manifest: Mapping[str, Any], amendments: Sequence[Mapping[str, Any]]
) -> tuple[str, ...]:
    """Problems of an amendment log against its bank (empty when the log is sound)."""
    problems: list[str] = []
    expected_bank = bank_sha256(manifest)
    options = _options(manifest)
    prev = expected_bank
    amended: set[tuple[str, str]] = set()
    for number, entry in enumerate(amendments, start=1):
        where = f"amendment {number}"
        problems.extend(f"{where}: {e}" for e in bank_amendment_errors(entry))
        if entry.get("bank_id") != manifest.get("bank_id"):
            problems.append(f"{where}: bank_id differs from the manifest")
        if entry.get("bank_sha256") != expected_bank:
            problems.append(f"{where}: bank_sha256 is not the manifest's bank hash")
        if entry.get("seq") != number:
            problems.append(f"{where}: seq must be {number}")
        if entry.get("prev_sha256") != prev:
            problems.append(f"{where}: prev_sha256 breaks the chain")
        prev = canonical_sha256(dict(entry))
        cell = (str(entry.get("profile")), str(entry.get("atom_id")))
        ranks = options.get(cell)
        if ranks is None:
            problems.append(f"{where}: no cell {cell}")
            continue
        if cell in amended:
            problems.append(f"{where}: cell {cell} was already amended (one reserve per cell)")
        amended.add(cell)
        if ranks.get(entry.get("replaced_rank", 0)) != entry.get("replaced_option_id"):
            problems.append(f"{where}: replaced_option_id is not the option at replaced_rank")
        if ranks.get(4) != entry.get("replacement_option_id"):
            problems.append(f"{where}: replacement_option_id is not the cell's reserve")
    return tuple(problems)


def effective_menu(
    manifest: Mapping[str, Any], amendments: Sequence[Mapping[str, Any]] = ()
) -> dict[tuple[str, str], tuple[str, ...]]:
    """`(profile, atom_id)` -> the 3 shown option IDs in rank order after amendments (the
    reserve takes the replaced option's rank). Raises `ValueError` for an unsound log."""
    problems = amendment_chain_errors(manifest, amendments)
    if problems:
        raise ValueError("; ".join(problems[:5]))
    menu = {cell: dict(ranks) for cell, ranks in _options(manifest).items()}
    for entry in amendments:
        ranks = menu[(entry["profile"], entry["atom_id"])]
        ranks[entry["replaced_rank"]] = ranks.pop(4)
    return {cell: tuple(ranks[r] for r in (1, 2, 3)) for cell, ranks in menu.items()}
