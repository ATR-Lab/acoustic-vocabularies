"""`banks amend`: the Study B §4 reserve rule.

A cell's reserve (rank 4) may replace an unusable *unheard, uncommitted* menu option
(rank 1-3) only before either partner hears that wave's menu. Neither condition is
visible in the bank directory (the session ledgers are #70's), so the operator must
confirm it (`unheard_confirmed=True`, CLI `--unheard-confirmed`); the command refuses
otherwise.

Before appending, `amend` verifies the whole bank (`verify_bank`: hashes, provenance,
compatibility) and rechecks the reserve: its recipe re-renders to its waveform hash, its
WAV file matches, it is technically valid, and it is compatible with every option of a
different atom under the profile (60 pairs: all 4 options of the 15 other atoms, a
superset of any effective menu). One amendment per cell; the manifest and its bank hash
never change. The amendment is appended to `amendments.jsonl` as a `bank_amendment`
record chained by `prev_sha256` (`bank-amendment.schema.json`), and consumers apply the
log with `av_generation.bank_manifest.effective_menu`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from av_generation.bank_manifest import (
    amendment_chain_errors,
    bank_amendment_errors,
    effective_menu,
)
from av_generation.clock import Clock, SystemClock
from av_generation.constants import B_SHOWN_OPTIONS
from av_generation.genconfig import GenerationConfig
from av_generation.jsonio import JsonlAppender, canonical_sha256, file_sha256
from av_sound.features import parse_threshold
from av_sound.recipe import Profile, Recipe
from av_sound.renderer import RENDERER_VERSION
from av_sound.reserved import ReservedRegistry
from av_sound.validate import VALIDATOR_VERSION, validate
from av_sound.wav import file_sha256 as wav_file_sha256

from av_banks.layout import BankLayout
from av_banks.manifest import read_amendments, read_manifest
from av_banks.verify import check_against, verify_bank

REASON_RE: Final = re.compile(r"[ -~]{1,200}")
DATE_RE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

E_HEARD: Final = "E_HEARD"
E_BANK: Final = "E_BANK"
E_CELL: Final = "E_CELL"
E_RECHECK: Final = "E_RECHECK"
E_INPUT: Final = "E_INPUT"


class AmendError(ValueError):
    """`amend` refused; `.code` names the rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class AmendResult:
    """The appended amendment and the cell's menu after it."""

    entry: dict[str, Any]
    menu: tuple[str, ...]
    """The cell's 3 shown option IDs after the amendment, in rank order."""


def amend_bank(
    path: str | os.PathLike[str],
    *,
    profile: str,
    atom_id: str,
    rank: int,
    reason: str,
    unheard_confirmed: bool,
    date: str | None = None,
    clock: Clock | None = None,
    reserved: ReservedRegistry | None = None,
) -> AmendResult:
    """Replace the menu option of `rank` (1-3) in a cell with the cell's reserve."""
    if not unheard_confirmed:
        raise AmendError(
            E_HEARD,
            "the reserve may replace only an unheard, uncommitted option before either partner "
            "hears the wave's menu; confirm it (--unheard-confirmed) or do not amend",
        )
    if isinstance(rank, bool) or rank not in range(1, B_SHOWN_OPTIONS + 1):
        raise AmendError(E_INPUT, f"rank must be 1..{B_SHOWN_OPTIONS} (a shown option)")
    if not REASON_RE.fullmatch(reason):
        raise AmendError(E_INPUT, "reason must be 1-200 printable ASCII characters")
    when = date if date is not None else (clock or SystemClock()).utc_now().date().isoformat()
    if not DATE_RE.fullmatch(when):
        raise AmendError(E_INPUT, f"date {when!r} must be YYYY-MM-DD")
    layout = BankLayout(Path(path))
    report = verify_bank(layout.root, reserved=reserved)
    if not report.ok:
        raise AmendError(E_BANK, f"the bank does not verify: {report.problems[:3]}")
    manifest = read_manifest(layout.manifest)
    if manifest.status != "complete":
        raise AmendError(E_BANK, f"bank {manifest.bank_id} is {manifest.status}")
    document = manifest.to_dict()
    amendments = read_amendments(layout.amendments)
    try:
        cell = manifest.cell(profile, atom_id)
    except KeyError as err:
        raise AmendError(E_CELL, str(err)) from None
    if any(a.get("profile") == profile and a.get("atom_id") == atom_id for a in amendments):
        raise AmendError(E_CELL, f"{profile} {atom_id} was already amended (one reserve per cell)")
    config = GenerationConfig.read(layout.generation_config)
    threshold = parse_threshold(config.separation_threshold)
    replaced = cell.options[rank - 1]
    reserve = cell.options[-1]
    recipe = Recipe.from_dict(reserve.recipe)
    result = validate(recipe, Profile(profile), (), reserved=reserved, threshold=threshold)
    problems = []
    if not result.ok:
        problems.append(f"the reserve is not technically valid {result.codes}")
    if result.pcm_sha256 != reserve.pcm_sha256:
        problems.append("the reserve does not re-render to its waveform hash")
    elif result.rendered is not None and wav_file_sha256(result.rendered) != reserve.file_sha256:
        problems.append("the reserve's canonical WAV differs from its file hash")
    if file_sha256(layout.option(reserve.wav)) != reserve.file_sha256:
        problems.append(f"{reserve.wav} differs from its file hash")
    others = [
        (o.option_id, c.atom_id, Recipe.from_dict(o.recipe), o.pcm_sha256)
        for c in manifest.cells
        if c.profile == profile and c.atom_id != atom_id
        for o in c.options
    ]
    check = check_against(
        (reserve.option_id, atom_id, recipe, reserve.pcm_sha256), others, threshold
    )
    problems.extend(check.failures)
    if problems:
        raise AmendError(E_RECHECK, "; ".join(problems[:5]))
    bank_hash = manifest.bank_sha256()
    entry: dict[str, Any] = {
        "record": "bank_amendment",
        "record_version": 1,
        "bank_id": manifest.bank_id,
        "bank_sha256": bank_hash,
        "seq": len(amendments) + 1,
        "prev_sha256": canonical_sha256(amendments[-1]) if amendments else bank_hash,
        "profile": profile,
        "atom_id": atom_id,
        "replaced_rank": rank,
        "replaced_option_id": replaced.option_id,
        "replacement_option_id": reserve.option_id,
        "recheck": {
            "ok": True,
            "pairs_checked": check.pairs,
            "threshold": config.separation_threshold,
            "renderer_version": RENDERER_VERSION,
            "validator_version": VALIDATOR_VERSION,
        },
        "reason": reason,
        "date": when,
    }
    errors = bank_amendment_errors(entry) + amendment_chain_errors(document, [*amendments, entry])
    if errors:  # pragma: no cover - the entry is built to the schema and the chain rules
        raise AmendError(E_RECHECK, "; ".join(errors[:5]))
    JsonlAppender(layout.amendments).append_obj(entry)
    menu = effective_menu(document, [*amendments, entry])[(profile, atom_id)]
    return AmendResult(entry, menu)
