"""`banks verify`: re-render a built bank from its recipes and recheck everything.

Checks (every problem is reported, none stops the others):

- the manifest matches its schema, and the manifest derived from the stored files
  (`manifest.manifest_from_files`) is identical, so the bank hash recomputed from the
  stored files equals the stored one (and `bank-sha256.txt`);
- the generation config is the one named by the manifest, and the running renderer and
  validator are the ones it pins (else re-rendering cannot reproduce the hashes);
- every attempt's slot log: hash and count as listed, slot IDs and B seed keys
  consistent, at most 12 slots per cell and 576 per attempt, cells traversed in the
  stored order, retention stopped at 4, a failed attempt's cell used 12 slots;
- provenance: every option comes from a `valid` slot record of the attempt used, with the
  same recipe and hashes;
- every option re-renders to its waveform hash, its canonical WAV equals the stored file
  and its hash, and it is technically valid (`av_sound.validate` without references);
- the 4 options of a cell have distinct waveforms;
- compatibility: under each profile, every pair of options of different atoms
  (64 options, 1,920 pairs) has different waveforms and passes the frozen separation
  rule (distance at least the config threshold, decided exactly);
- the amendment log, if any, is a sound chain over the bank hash
  (`bank_manifest.amendment_chain_errors`) and rechecked at the config threshold.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from av_generation.bank_manifest import amendment_chain_errors
from av_generation.constants import (
    B_OPTIONS_PER_CELL,
    B_SLOTS_PER_ATTEMPT,
    B_SLOTS_PER_CELL,
    PROFILES,
)
from av_generation.genconfig import GenerationConfig, config_differences
from av_generation.ids import IdError, parse_bank_slot_id
from av_generation.jsonio import file_sha256, to_json_value
from av_generation.outcomes import SlotOutcome
from av_generation.records import SlotRecord, read_records
from av_generation.seeds import b_seed_key
from av_sound.features import N_FEATURES, features, parse_threshold, sum_squared_diff
from av_sound.recipe import Profile, Recipe, RecipeError
from av_sound.reserved import ReservedRegistry, load_reserved_registry
from av_sound.validate import validate
from av_sound.wav import file_sha256 as wav_file_sha256
from av_sound.wav import read_wav

from av_banks.layout import BankLayout
from av_banks.manifest import (
    AttemptSummary,
    BankManifest,
    ManifestError,
    manifest_from_files,
    read_amendments,
)

PAIRS_PER_PROFILE = 16 * 15 // 2 * B_OPTIONS_PER_CELL * B_OPTIONS_PER_CELL
"""Different-atom option pairs under one profile: 120 atom pairs x 16 option pairs = 1,920."""


@dataclass(frozen=True, slots=True)
class PairCheck:
    """Compatibility of every different-atom pair of a set of options under one profile."""

    pairs: int
    failures: tuple[str, ...]


def check_pairs(options: Sequence[tuple[str, str, Recipe, str]], threshold: Fraction) -> PairCheck:
    """`options` = (option_id, atom_id, recipe, pcm_sha256); pairs of the same atom are
    skipped. A pair fails on an identical waveform or a distance below `threshold`."""
    limit = N_FEATURES * threshold * threshold
    feats = [features(recipe) for _, _, recipe, _ in options]
    pairs = 0
    failures = []
    for i in range(len(options)):
        for j in range(i + 1, len(options)):
            if options[i][1] == options[j][1]:
                continue
            pairs += 1
            if options[i][3] == options[j][3]:
                failures.append(f"{options[i][0]} and {options[j][0]}: identical waveforms")
            elif sum_squared_diff(feats[i], feats[j]) < limit:
                failures.append(f"{options[i][0]} and {options[j][0]}: closer than {threshold}")
    return PairCheck(pairs, tuple(failures))


def check_against(
    candidate: tuple[str, str, Recipe, str],
    options: Sequence[tuple[str, str, Recipe, str]],
    threshold: Fraction,
) -> PairCheck:
    """Compatibility of one option with every option of another atom in `options`."""
    limit = N_FEATURES * threshold * threshold
    own = features(candidate[2])
    pairs = 0
    failures = []
    for option_id, atom_id, recipe, pcm in options:
        if atom_id == candidate[1]:
            continue
        pairs += 1
        if pcm == candidate[3]:
            failures.append(f"{candidate[0]} and {option_id}: identical waveforms")
        elif sum_squared_diff(own, features(recipe)) < limit:
            failures.append(f"{candidate[0]} and {option_id}: closer than {threshold}")
    return PairCheck(pairs, tuple(failures))


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """The result of `verify_bank` (`ok` exactly when `problems` is empty)."""

    bank_id: str
    status: str | None
    bank_sha256: str | None
    """The bank hash recomputed from the stored files (`None` if they give no manifest)."""
    ok: bool
    problems: tuple[str, ...]
    attempts_checked: int
    slots_checked: int
    options_checked: int
    pairs_checked: Mapping[str, int] = field(default_factory=dict)
    pairs_failed: Mapping[str, int] = field(default_factory=dict)
    amendments_checked: int = 0

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        return data


class _Checker:
    def __init__(self, layout: BankLayout, reserved: ReservedRegistry | None) -> None:
        self.layout = layout
        self.reserved = reserved if reserved is not None else load_reserved_registry()
        self.problems: list[str] = []
        self.slots = 0
        self.n_options = 0
        self.pairs: dict[str, int] = {}
        self.pairs_failed: dict[str, int] = {}
        self.records: dict[int, list[SlotRecord]] = {}

    def problem(self, text: str) -> None:
        self.problems.append(text)

    # -- attempts ------------------------------------------------------------

    def attempt(self, manifest: BankManifest, number: int) -> None:
        layout = self.layout
        where = f"attempt {number}"
        try:
            records = read_records(layout.slots(number), SlotRecord)
            summary = AttemptSummary.read(layout.attempt_summary(number))
        except (OSError, ValueError) as err:
            self.problem(f"{where}: {err}")
            return
        self.records[number] = records
        self.slots += len(records)
        if summary.slots_sha256 != file_sha256(layout.slots(number)):
            self.problem(f"{where}: slots.jsonl differs from the hash in attempt.json")
        if summary.slots_used != len(records):
            self.problem(
                f"{where}: attempt.json counts {summary.slots_used} slots, log has {len(records)}"
            )
        if len(records) > B_SLOTS_PER_ATTEMPT:
            self.problem(f"{where}: {len(records)} slots (cap {B_SLOTS_PER_ATTEMPT})")
        cells: dict[tuple[str, str], list[SlotRecord]] = {}
        for record in records:
            self.record(manifest, number, record)
            cells.setdefault((record.profile.value, record.atom_id), []).append(record)
        order = {a: i for i, a in enumerate(manifest.atom_order)}
        for (profile, atom), cell in sorted(cells.items()):
            slots = [r.slot for r in cell]
            if slots != list(range(1, len(cell) + 1)):
                self.problem(f"{where} {profile} {atom}: slots {slots} are not 1..n in order")
            if len(cell) > B_SLOTS_PER_CELL:
                self.problem(f"{where} {profile} {atom}: {len(cell)} slots (cap 12)")
            valid = [i for i, r in enumerate(cell) if r.outcome is SlotOutcome.VALID]
            if len(valid) > B_OPTIONS_PER_CELL:
                self.problem(f"{where} {profile} {atom}: more than 4 valid slots")
            if len(valid) == B_OPTIONS_PER_CELL and valid[-1] != len(cell) - 1:
                self.problem(f"{where} {profile} {atom}: slots continued after the 4th option")
        for profile in PROFILES:
            started = sorted(
                (order[a] for (p, a) in cells if p == profile and a in order), reverse=True
            )
            if started and started != list(range(started[0], -1, -1)):
                self.problem(f"{where} {profile}: cells were not traversed in the stored order")
            for index in started[1:]:
                atom = manifest.atom_order[index]
                n_valid = sum(r.outcome is SlotOutcome.VALID for r in cells[(profile, atom)])
                if n_valid != B_OPTIONS_PER_CELL:
                    self.problem(f"{where} {profile} {atom}: traversal moved on without 4 options")
        if summary.status == "complete":
            for profile in PROFILES:
                for atom in manifest.atom_order:
                    n_valid = sum(
                        r.outcome is SlotOutcome.VALID for r in cells.get((profile, atom), [])
                    )
                    if n_valid != B_OPTIONS_PER_CELL:
                        self.problem(f"{where}: complete, but {profile} {atom} has {n_valid}")
        elif summary.failed_cell is None:
            self.problem(f"{where}: failed without a failed cell")
        else:
            failed = cells.get((summary.failed_cell.profile, summary.failed_cell.atom_id), [])
            n_valid = sum(r.outcome is SlotOutcome.VALID for r in failed)
            if len(failed) != B_SLOTS_PER_CELL or n_valid >= B_OPTIONS_PER_CELL:
                self.problem(
                    f"{where}: failed cell {summary.failed_cell.profile} "
                    f"{summary.failed_cell.atom_id} has {len(failed)} slots and {n_valid} options"
                )

    def record(self, manifest: BankManifest, number: int, record: SlotRecord) -> None:
        where = record.slot_id
        try:
            parsed = parse_bank_slot_id(record.slot_id)
        except IdError as err:
            self.problem(f"attempt {number}: {err}")
            return
        expected = (manifest.bank_id, number, record.profile.value, record.atom_id, record.slot)
        if tuple(parsed) != expected or record.bank_id != manifest.bank_id:
            self.problem(f"{where}: slot ID, bank, attempt or cell fields disagree")
        if record.attempt != number:
            self.problem(f"{where}: logged in attempt {number} but names attempt {record.attempt}")
        key = b_seed_key(
            manifest.seed_namespace, number, record.profile.value, record.atom_id, record.slot
        )
        if record.seed_key != key:
            self.problem(f"{where}: seed key {record.seed_key!r} is not {key!r}")
        if record.outcome is SlotOutcome.VALID and (
            record.recipe is None or record.pcm_sha256 is None or record.file_sha256 is None
        ):
            self.problem(f"{where}: valid slot without recipe or hashes")

    # -- options -----------------------------------------------------------

    def options(self, manifest: BankManifest, threshold: Fraction) -> None:
        used = manifest.attempt_used
        records = {r.slot_id: r for r in self.records.get(used or 0, [])}
        for profile in PROFILES:
            entries: list[tuple[str, str, Recipe, str]] = []
            for cell in (c for c in manifest.cells if c.profile == profile):
                hashes = [o.pcm_sha256 for o in cell.options]
                if len(set(hashes)) != len(hashes):
                    self.problem(f"{profile} {cell.atom_id}: options share a waveform")
                for option in cell.options:
                    recipe = self.option(manifest, option, profile, records, threshold)
                    if recipe is not None:
                        entries.append((option.option_id, cell.atom_id, recipe, option.pcm_sha256))
            check = check_pairs(entries, threshold)
            self.pairs[profile] = check.pairs
            self.pairs_failed[profile] = len(check.failures)
            for failure in check.failures[:10]:
                self.problem(f"{profile}: incompatible options {failure}")
            if manifest.status == "complete" and check.pairs != PAIRS_PER_PROFILE:
                self.problem(f"{profile}: {check.pairs} different-atom pairs, expected 1920")

    def option(
        self,
        manifest: BankManifest,
        option: Any,  # noqa: ANN401 - ManifestOption
        profile: str,
        records: Mapping[str, SlotRecord],
        threshold: Fraction,
    ) -> Recipe | None:
        where = option.option_id
        self.n_options += 1
        try:
            parsed = parse_bank_slot_id(option.slot_id)
        except IdError as err:
            self.problem(f"{where}: {err}")
            parsed = None
        if parsed is not None and parsed.attempt != manifest.attempt_used:
            self.problem(f"{where}: from attempt {parsed.attempt}, not the attempt used")
        record = records.get(option.slot_id)
        if record is None or record.outcome is not SlotOutcome.VALID:
            self.problem(f"{where}: no valid slot record {option.slot_id} in the attempt used")
        elif (
            dict(record.recipe or {}) != dict(option.recipe)
            or record.pcm_sha256 != option.pcm_sha256
            or record.file_sha256 != option.file_sha256
        ):
            self.problem(f"{where}: differs from its slot record")
        try:
            recipe = Recipe.from_dict(option.recipe)
        except RecipeError as err:
            self.problem(f"{where}: bad recipe ({err})")
            return None
        if recipe.sha256() != option.recipe_sha256:
            self.problem(f"{where}: recipe_sha256 does not match the recipe")
        result = validate(recipe, Profile(profile), (), reserved=self.reserved, threshold=threshold)
        if not result.ok:
            self.problem(f"{where}: not technically valid {result.codes}")
        if result.pcm_sha256 != option.pcm_sha256:
            self.problem(f"{where}: re-rendering gives waveform {result.pcm_sha256}")
        elif result.rendered is not None and wav_file_sha256(result.rendered) != option.file_sha256:
            self.problem(f"{where}: canonical WAV hash differs from file_sha256")
        path = self.layout.option(option.wav)
        if not path.is_file():
            self.problem(f"{where}: {option.wav} is missing")
        else:
            if file_sha256(path) != option.file_sha256:
                self.problem(f"{where}: {option.wav} differs from file_sha256")
            try:
                read_wav(path)
            except ValueError as err:
                self.problem(f"{where}: {option.wav}: {err}")
        return recipe


def verify_bank(
    path: str | os.PathLike[str], *, reserved: ReservedRegistry | None = None
) -> VerifyReport:
    """Verify a bank directory (see the module docstring)."""
    layout = BankLayout(Path(path))
    checker = _Checker(layout, reserved)
    try:
        manifest = BankManifest.read(layout.manifest)
    except (OSError, ValueError) as err:
        return VerifyReport(layout.bank_id, None, None, False, (f"manifest: {err}",), 0, 0, 0)
    stored = manifest.bank_sha256()
    recomputed: str | None = None
    try:
        derived = manifest_from_files(layout.root)
        recomputed = derived.bank_sha256()
        if derived.to_dict() != manifest.to_dict():
            fields = sorted(
                k for k, v in derived.to_dict().items() if manifest.to_dict().get(k) != v
            )
            checker.problem(f"manifest.json differs from the stored files in {fields}")
    except (OSError, ValueError, ManifestError) as err:
        checker.problem(f"stored files do not give a manifest: {err}")
    if recomputed is not None and recomputed != stored:
        checker.problem(f"bank hash {stored} != {recomputed} recomputed from the stored files")
    if layout.bank_hash.is_file():
        written = layout.bank_hash.read_text(encoding="utf-8").strip()
        if written != stored:
            checker.problem(
                f"{layout.bank_hash.name} holds {written}, the manifest hashes to {stored}"
            )
    else:
        checker.problem(f"{layout.bank_hash.name} is missing")
    threshold: Fraction | None = None
    try:
        config = GenerationConfig.read(layout.generation_config)
        if config.frozen_sha256() != manifest.generation_config_sha256:
            checker.problem("generation-config.json is not the config named by the manifest")
        differences = config_differences(config)
        if differences:
            checker.problem(f"the running code differs from the bank's config in {differences}")
        threshold = parse_threshold(config.separation_threshold)
    except (OSError, ValueError) as err:
        checker.problem(f"generation config: {err}")
    for entry in manifest.attempts:
        checker.attempt(manifest, entry.attempt)
    if threshold is not None:
        checker.options(manifest, threshold)
    amendments: tuple[dict[str, Any], ...] = ()
    try:
        amendments = read_amendments(layout.amendments)
    except (OSError, ValueError) as err:
        checker.problem(f"amendments: {err}")
    if amendments:
        checker.problems.extend(amendment_chain_errors(manifest.to_dict(), amendments))
        if threshold is not None:
            for number, amendment in enumerate(amendments, start=1):
                recheck = amendment.get("recheck") or {}
                try:
                    same = parse_threshold(str(recheck.get("threshold"))) == threshold
                except ValueError:
                    same = False
                if not same:
                    checker.problem(f"amendment {number}: rechecked at another threshold")
    return VerifyReport(
        bank_id=manifest.bank_id,
        status=manifest.status,
        bank_sha256=recomputed,
        ok=not checker.problems,
        problems=tuple(checker.problems),
        attempts_checked=len(checker.records),
        slots_checked=checker.slots,
        options_checked=checker.n_options,
        pairs_checked=dict(checker.pairs),
        pairs_failed=dict(checker.pairs_failed),
        amendments_checked=len(amendments),
    )
