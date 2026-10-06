"""Synthetic Study A batch logs for the audit tests and the DEMO audit example (#24).

`write_synthetic_batch(runs_root, spec)` writes one run directory (run manifest, batch
config, slot/rating/decision/commit/fallback-scan/timing logs) with the shared record
types. It runs no component: it is a log fixture for `av_generation.audit`, not the
synthetic-panel dry run (#22), and its numbers mean nothing. Everything is `DEMO-` and
deterministic (one PCG64 stream from `seeds.bot_seed_key(run_id, "synth", "logs")`, a
synthetic run clock, fixed UTC texts), so the same spec gives the same bytes on every
platform. Hashes of audio that was never rendered are labelled SHA-256 values of text.

Cases built in (positions are 1..16 in the batch's atom order):

- `bank_fallback`: one book's candidates of one atom are all rated unacceptable, so the
  bank scan commits the first unused bank recipe (`fallback_bank`);
- `book_fallback`: one book's atom has no eligible candidate and its scan is exhausted,
  so the frozen fallback book replaces the book (16 `fallback_book` commits, a
  `book_substituted` event); the method keeps generating (`archive`, `archive_none`);
- `archive_none`: after the substitution, one more zero-eligible atom of that book;
- `resume_at`: the batch resumes in a new process at that appointment, so the run clock
  restarts at 0 (durations must come from paired events, never across a restart);
- per-method outcome mixes (timeouts, validator rejections, model server errors, output
  overflows, one input overflow) and missing ratings.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from av_sound import load_fallback
from av_sound._paths import data_root as sound_data_root
from av_sound.fallback import FallbackSet
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS, Recipe

from av_generation._paths import examples_path
from av_generation.audit import build_audit, tally_sheet
from av_generation.config import BatchConfig
from av_generation.constants import (
    ATOMS_PER_APPOINTMENT,
    MIN_ACCEPTABLE_COMFORT,
    RATING_SLOT_MS,
    RATING_WINDOW_MS,
    ROUNDS_PER_ATOM,
    SLOT_CAP_MS,
    SLOTS_PER_ROUND,
)
from av_generation.ids import (
    PANEL_ALIAS_ALPHABET,
    Method,
    RunKind,
    Study,
    proposal_slot_id,
    rating_slot_id,
    slot_index,
)
from av_generation.jsonio import canonical_sha256, document_text, read_json
from av_generation.outcomes import LlmStatus, SlotOutcome
from av_generation.records import (
    A2Detail,
    A2Mutation,
    CandidateScore,
    CommitRecord,
    DecisionRecord,
    FallbackScanRecord,
    RatingRecord,
    Record,
    RecordWriter,
    RunBook,
    RunCode,
    RunManifest,
    SlotRecord,
    TimingEvent,
)
from av_generation.rundir import RunLayout, create_run_dir
from av_generation.seeds import a2_seed_key, a3_seed_key, bot_seed_key, rng_for, seed_from_key

DEMO_UTC: Final = "2026-11-05T09:00:00.000Z"
CLOSED_UTC: Final = "2026-11-05T17:00:00.000Z"
FALLBACK_MANIFEST: Final = "testvectors/fallback/demo-manifest.json"
SYNTH_CODE: Final = RunCode(
    av_generation="0.1.0",
    renderer_version="0.1.0",
    renderer_hash=hashlib.sha256(b"synthetic renderer").hexdigest(),
    validator_version="0.1.0",
    validator_hash=hashlib.sha256(b"synthetic validator").hexdigest(),
)
"""Fixed code versions of the synthetic run (labelled hashes of text), so the DEMO
example's hashes do not change when unrelated code versions change."""

_OUTCOME_MIX: Final[dict[Method, tuple[tuple[str, float], ...]]] = {
    Method.A1: (
        ("valid", 0.82),
        ("timeout", 0.05),
        ("event_too_short", 0.05),
        ("separation_fail", 0.05),
        ("duplicate", 0.03),
    ),
    Method.A2: (
        ("valid", 0.70),
        ("separation_fail", 0.14),
        ("event_too_short", 0.09),
        ("duplicate", 0.04),
        ("clipping", 0.03),
    ),
    Method.A3: (
        ("valid", 0.58),
        ("invalid_json", 0.08),
        ("server_error", 0.02),
        ("schema_violation", 0.07),
        ("out_of_domain", 0.08),
        ("separation_fail", 0.08),
        ("event_too_short", 0.04),
        ("duplicate", 0.02),
        ("overflow_output", 0.02),
        ("timeout", 0.01),
    ),
}
_CODES: Final[dict[str, tuple[str, ...]]] = {
    "separation_fail": ("E_SEPARATION",),
    "event_too_short": ("E_EVENT_SHORT",),
    "duplicate": ("E_DUPLICATE",),
    "clipping": ("E_CLIP",),
    "schema_violation": ("E_SCHEMA",),
    "out_of_domain": ("E_DOMAIN",),
}
_WITH_RECIPE: Final = frozenset(
    {"valid", "separation_fail", "event_too_short", "duplicate", "clipping"}
)


@dataclass(frozen=True, slots=True)
class SynthSpec:
    """What `write_synthetic_batch` writes (defaults: the DEMO audit example)."""

    run_id: str = "DEMO-AUDIT-01"
    batch: int = 1
    """Batch 1 is `generation/examples/demo-batch-config.json` (`DEMO-A-P01`); other
    numbers get `DEMO-A-P<nn>` and their own DEMO book IDs."""
    bank_fallback: tuple[Method, int] | None = (Method.A2, 3)
    book_fallback: tuple[Method, int] | None = (Method.A1, 7)
    archive_none: tuple[Method, int] | None = (Method.A1, 12)
    overflow_input: tuple[Method, int] | None = (Method.A3, 16)
    """Round 4, slot 3 of this atom: a prompt above the input limit (no model call)."""
    resume_at: int | None = 3
    closed: bool = True
    incomplete: bool = False
    """Log a rater withdrawal and `batch_incomplete` (the run is left out of set tables)."""


def demo_config(batch: int = 1) -> BatchConfig:
    """The DEMO batch config of batch `batch` (batch 1 is the committed example)."""
    data = read_json(examples_path("demo-batch-config.json"))
    if batch != 1:
        letters = PANEL_ALIAS_ALPHABET
        renames: dict[str, str] = {}
        for i, book in enumerate(data["books"]):
            code = "".join(letters[(batch * 7 + i * 5 + k * 3) % len(letters)] for k in range(4))
            renames[book["book_id"]] = f"DEMO-BK-{code}"
            book["book_id"] = renames[book["book_id"]]
        data["batch_id"] = data["seed_namespace"] = f"DEMO-A-P{batch:02d}"
        panel = data["panel"]
        panel["order"] = [renames[b] for b in panel["order"]]
        panel["aliases"] = {renames[b]: a for b, a in panel["aliases"].items()}
        panel["panel_id"] = f"DEMO-PANEL-{batch:02d}"
    return BatchConfig.from_dict(data).check_consistency()


def _text_sha256(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


class _Writers:
    def __init__(self, layout: RunLayout, *, validate: bool) -> None:
        self._layout = layout
        self._validate = validate
        self._writers: dict[str, RecordWriter] = {}

    def __call__(self, record: Record) -> None:
        tag = type(record).TAG
        writer = self._writers.get(tag)
        if writer is None:
            writer = RecordWriter(self._layout.log(tag), validate=self._validate, fsync=False)
            self._writers[tag] = writer
        writer.append(record)


class _Synth:
    def __init__(
        self, spec: SynthSpec, config: BatchConfig, layout: RunLayout, *, validate: bool
    ) -> None:
        self.spec = spec
        self.config = config
        self.run_id = spec.run_id
        self.write = _Writers(layout, validate=validate)
        self.rng = rng_for(bot_seed_key(spec.run_id, "synth", "logs"))
        self.t = 0
        self.fallback: FallbackSet = load_fallback(sound_data_root() / FALLBACK_MANIFEST)
        self.books = {b.method: b for b in config.books}
        self.store_book = {b.book_id: b.book_id for b in config.books}
        self.substituted: set[str] = set()
        self.committed: dict[str, list[CommitRecord]] = {b.book_id: [] for b in config.books}
        self.used_bank: dict[str, list[int]] = {b.book_id: [] for b in config.books}
        self.heads: dict[str, str] = {}

    # Helpers ------------------------------------------------------------
    def _rand(self, low: int, high: int) -> int:
        return int(self.rng.integers(low, high + 1))

    def _event(self, event: str, **fields: Any) -> None:  # noqa: ANN401
        self.write(
            TimingEvent(run_id=self.run_id, event=event, t_ms=self.t, batch_id=self.batch, **fields)
        )

    @property
    def batch(self) -> str:
        return self.config.batch_id

    def _span(self, start: str, end: str, duration: int, **fields: Any) -> None:  # noqa: ANN401
        self._event(start, **fields)
        self.t += duration
        self._event(end, **fields)

    def _recipe(self) -> Recipe:
        r = self.rng
        return Recipe(
            total_ms=TOTAL_MS[int(r.integers(len(TOTAL_MS)))],
            pitches=tuple(PITCHES[int(i)] for i in r.integers(len(PITCHES), size=3)),  # type: ignore[arg-type]
            rhythm_weights=tuple(  # type: ignore[arg-type]
                RHYTHM_WEIGHTS[int(i)] for i in r.integers(len(RHYTHM_WEIGHTS), size=3)
            ),
            gaps_ms=tuple(GAPS_MS[int(i)] for i in r.integers(len(GAPS_MS), size=2)),  # type: ignore[arg-type]
            amplitudes=tuple(AMPLITUDES[int(i)] for i in r.integers(len(AMPLITUDES), size=3)),  # type: ignore[arg-type]
        )

    def _draw(self, method: Method) -> str:
        x = float(self.rng.random())
        acc = 0.0
        mix = _OUTCOME_MIX[method]
        for name, p in mix:
            acc += p
            if x < acc:
                return name
        return mix[-1][0]

    def _position(self, atom: str) -> int:
        return self.config.atom_order.index(atom) + 1

    def _injected(self, case: tuple[Method, int] | None, method: Method, atom: str) -> bool:
        return case is not None and case == (method, self._position(atom))

    # Slots --------------------------------------------------------------
    def _slot(
        self, method: Method, atom: str, round_: int, slot: int, incumbent: str | None
    ) -> SlotRecord:
        book = self.books[method]
        sid = proposal_slot_id(book.book_id, atom, round_, slot)
        name = self._draw(method)
        if (
            method is Method.A3
            and round_ == ROUNDS_PER_ATOM
            and slot == SLOTS_PER_ROUND
            and self._injected(self.spec.overflow_input, method, atom)
        ):
            name = "overflow_input"
        outcome = SlotOutcome.INVALID_JSON if name == "server_error" else SlotOutcome(name)
        recipe = self._recipe() if name in _WITH_RECIPE else None
        fields: dict[str, Any] = {}
        pos = self._position(atom)
        if method is Method.A1:
            latency = SLOT_CAP_MS if name == "timeout" else self._rand(12_000, 38_000)
            fields.update(
                designer_id=book.designer_id,
                latency_ms=None if name == "timeout" else latency,
                design_ms=latency - (0 if name == "timeout" else self._rand(500, 3_000)),
                raw_output=recipe.canonical_json() if recipe else None,
            )
        elif method is Method.A2:
            latency = self._rand(1, 6)
            key = a2_seed_key(self.config.seed_namespace, atom, round_, slot)
            mutations = tuple(
                A2Mutation(f"pitch_{k}", 0, 1, 1, 1, False) for k in range(1, slot + 1)
            )
            detail = (
                A2Detail("mutation", incumbent, mutations)
                if round_ > 1 and incumbent is not None
                else A2Detail("uniform", None)
            )
            fields.update(
                seed_key=key,
                seed=seed_from_key(key),
                latency_ms=latency,
                raw_output=recipe.canonical_json() if recipe else None,
                a2=detail,
            )
        else:
            key = a3_seed_key(self.config.seed_namespace, atom, round_, slot)
            tokens_in = 900 + 70 * pos + 30 * round_ + self._rand(0, 40)
            status = {
                "server_error": LlmStatus.SERVER_ERROR,
                "timeout": LlmStatus.TIMEOUT,
                "overflow_output": LlmStatus.OVERFLOW_OUTPUT,
                "overflow_input": None,
            }.get(name, LlmStatus.OK)
            latency = {
                "server_error": self._rand(200, 800),
                "timeout": SLOT_CAP_MS,
                "overflow_output": self._rand(9_000, 14_000),
                "overflow_input": self._rand(20, 60),
            }.get(name, self._rand(1_500, 9_000))
            tokens_out = {
                "server_error": None,
                "timeout": None,
                "overflow_output": 512,
                "overflow_input": None,
            }.get(name, self._rand(40, 140))
            fields.update(
                seed_key=key,
                seed=seed_from_key(key),
                prompt_sha256=_text_sha256("synthetic prompt", sid),
                schema_sha256=_text_sha256("synthetic decoding schema"),
                llm_status=status,
                tokens_in=16_500 if name == "overflow_input" else tokens_in,
                tokens_out=tokens_out,
                latency_ms=None if name == "overflow_input" else latency,
                raw_output=(
                    recipe.canonical_json()
                    if recipe
                    else None
                    if status in (None, LlmStatus.SERVER_ERROR, LlmStatus.TIMEOUT)
                    else '{"synthetic": "not a recipe"}'
                ),
            )
        t_open = self.t
        spent = int(fields.get("latency_ms") or fields.get("design_ms") or 50)
        self.t += spent
        pcm = _text_sha256("synthetic pcm", recipe.sha256(), "P1") if recipe else None
        return SlotRecord(
            run_id=self.run_id,
            study=Study.A,
            method=method,
            slot_id=sid,
            profile=self.config.profile,
            atom_id=atom,
            slot=slot,
            slot_index=slot_index(round_, slot),
            outcome=outcome,
            t_open_ms=t_open,
            t_ms=self.t,
            batch_id=self.batch,
            book_id=book.book_id,
            round=round_,
            recipe=recipe.to_dict() if recipe else None,
            recipe_sha256=recipe.sha256() if recipe else None,
            validator_codes=_CODES.get(name, ()),
            validator_messages=tuple(f"synthetic {c}" for c in _CODES.get(name, ())),
            pcm_sha256=pcm if outcome is SlotOutcome.VALID else None,
            file_sha256=(
                _text_sha256("synthetic wav", pcm) if outcome is SlotOutcome.VALID else None
            ),
            **fields,
        )

    # Ratings and selector -----------------------------------------------
    def _rate(
        self, slot: SlotRecord, position: int, start: int, first: bool, forced: bool
    ) -> CandidateScore:
        valid = slot.outcome is SlotOutcome.VALID
        acceptable = 0
        submitted = 0
        score_sum = Fraction(0)
        score_raters = 0
        assert slot.round is not None and slot.book_id is not None
        for seat in self.config.panel.raters:
            missing = valid and float(self.rng.random()) < 0.02
            comfort: str | None = None
            association = distinguishability = None
            if valid and not missing:
                unacceptable = forced or float(self.rng.random()) >= 0.85
                comfort = "unacceptable" if unacceptable else "acceptable"
                association = self._rand(2, 7)
                distinguishability = 4 if first else self._rand(1, 7)
                submitted += 1
                acceptable += comfort == "acceptable"
                score_sum += Fraction(association + distinguishability, 2)
                score_raters += 1
            self.write(
                RatingRecord(
                    run_id=self.run_id,
                    batch_id=self.batch,
                    book_id=slot.book_id,
                    atom_id=slot.atom_id,
                    round=slot.round,
                    position=position,
                    rating_slot_id=rating_slot_id(self.batch, slot.atom_id, slot.round, position),
                    slot_id=slot.slot_id,
                    rater_id=seat.rater_id,
                    station=seat.station,
                    rater_kind=seat.kind,
                    placeholder=not valid,
                    first_atom=first,
                    association=association,
                    distinguishability=distinguishability,
                    distinguishability_by_rule=first and comfort is not None,
                    comfort=comfort,  # type: ignore[arg-type]
                    missing=missing,
                    reconnected=False,
                    slot_start_ms=start,
                    t_ms=start + RATING_SLOT_MS,
                    candidate_onset_ms=self._rand(5, 30) if valid else None,
                    reference_onset_ms=None if first or not valid else 2_000 + self._rand(5, 30),
                    unlock_ms=2_500 if valid else None,
                    rt_ms=self._rand(1_500, 9_000) if comfort is not None else None,
                )
            )
        eligible = valid and acceptable >= MIN_ACCEPTABLE_COMFORT
        score = score_sum / score_raters if score_raters else None
        return CandidateScore(
            slot_id=slot.slot_id,
            slot_index=slot.slot_index,
            technically_valid=valid,
            n_ratings=submitted,
            n_acceptable=acceptable,
            eligible=eligible,
            score=_fraction_text(score) if score is not None else None,
            score_raters=score_raters,
            flagged_missing=valid and submitted < len(self.config.panel.raters),
        )

    # Commits ----------------------------------------------------------------
    def _commit(
        self,
        book_id: str,
        atom: str,
        recipe: dict[str, Any],
        pcm: str,
        wav: str,
        *,
        source: str,
        store_source: str,
        slot_id: str | None = None,
        bank_index: int | None = None,
    ) -> None:
        store = self.store_book[book_id]
        recipe_sha = Recipe.from_dict(recipe).sha256()
        head = _text_sha256("synthetic chain", self.heads.get(store, "genesis"), recipe_sha)
        self.heads[store] = head
        record = CommitRecord(
            run_id=self.run_id,
            batch_id=self.batch,
            book_id=book_id,
            store_book_id=store,
            atom_id=atom,
            semantic_label=self.config.labels[atom],
            source=source,  # type: ignore[arg-type]
            store_source=store_source,
            recipe=recipe,
            recipe_sha256=recipe_sha,
            pcm_sha256=pcm,
            file_sha256=wav,
            chain_head=head,
            failed_generation=source == "fallback_book",
            t_ms=self.t,
            slot_id=slot_id,
            bank_index=bank_index,
        )
        self.write(record)
        self.committed[book_id].append(record)

    def _scan(self, book_id: str, atom: str, exhausted: bool) -> int | None:
        bank = self.fallback.bank(self.config.profile)
        used = sorted(self.used_bank[book_id])
        refs = [[c.atom_id, c.recipe_sha256, c.pcm_sha256] for c in self.committed[book_id]]
        log: list[dict[str, Any]] = []
        selected: int | None = None
        for entry in bank:
            step: dict[str, Any] = {
                "index": entry.index,
                "recipe_sha256": entry.recipe.sha256(),
                "pcm_sha256": entry.pcm_sha256,
                "codes": [],
                "messages": [],
            }
            if entry.index in used:
                step["outcome"] = "used"
            elif exhausted:
                step.update(outcome="rejected", codes=["E_SEPARATION"], messages=["synthetic"])
            else:
                step["outcome"] = "selected"
                selected = entry.index
            log.append(step)
            if selected is not None:
                break
        chosen = bank[selected] if selected is not None else None
        scan = {
            "scan_version": 1,
            "profile": self.config.profile.value,
            "bank_sha256": bank.bank_sha256,
            "bank_threshold": self.config.threshold,
            "threshold": self.config.threshold,
            "reserved_sha256": _text_sha256("synthetic reserved registry"),
            "validator_version": SYNTH_CODE.validator_version,
            "renderer_version": SYNTH_CODE.renderer_version,
            "reference_ids": [r[0] for r in refs],
            "references_sha256": canonical_sha256(refs),
            "used": used,
            "outcome": "selected" if chosen else "exhausted",
            "selected_index": selected,
            "selected_source": chosen.source if chosen else None,
            "selected_recipe_sha256": chosen.recipe.sha256() if chosen else None,
            "selected_pcm_sha256": chosen.pcm_sha256 if chosen else None,
            "log": log,
        }
        self.write(
            FallbackScanRecord(
                run_id=self.run_id,
                batch_id=self.batch,
                book_id=book_id,
                atom_id=atom,
                scan=scan,
                t_ms=self.t,
            )
        )
        return selected

    def _substitute(self, book_id: str, atom: str) -> None:
        self.store_book[book_id] = "DEMO-FB-" + book_id.rsplit("-", 1)[-1]
        self.substituted.add(book_id)
        fallback_book = self.fallback.book(self.config.profile)
        for target in self.config.atom_order:
            entry = fallback_book.atom(target)
            self.t += 5
            self._commit(
                book_id,
                target,
                entry.recipe.to_dict(),
                entry.pcm_sha256,
                entry.file_sha256,
                source="fallback_book",
                store_source=entry.source,
            )
        self._event(
            "book_substituted",
            book_id=book_id,
            atom_id=atom,
            component="orchestrator",
            detail="bank scan exhausted: fallback book committed",
        )

    # Rounds ------------------------------------------------------------------
    def _atom(self, atom: str, first: bool) -> None:
        self._event("atom_start", atom_id=atom, component="orchestrator")
        incumbents: dict[str, tuple[Fraction, int, SlotRecord] | None] = {
            b.book_id: None for b in self.config.books
        }
        for round_ in range(1, ROUNDS_PER_ATOM + 1):
            self._event("round_start", atom_id=atom, round=round_, component="orchestrator")
            window = self.t
            self._event("proposal_window_start", atom_id=atom, round=round_)
            slots: dict[str, list[SlotRecord]] = {}
            ends = []
            for book in self.config.books:
                self.t = window
                best = incumbents[book.book_id]
                slots[book.book_id] = [
                    self._slot(book.method, atom, round_, s, best[2].slot_id if best else None)
                    for s in range(1, SLOTS_PER_ROUND + 1)
                ]
                ends.append(self.t)
            for book_slots in slots.values():
                for record in book_slots:
                    self.write(record)
            self.t = max(ends) + 400
            self._event("proposal_window_end", atom_id=atom, round=round_)
            rating_start = self.t
            self._event("rating_window_start", atom_id=atom, round=round_, component="panel")
            scores: dict[str, list[CandidateScore]] = {b: [] for b in slots}
            for position in range(1, 10):
                book_id = self.config.panel.order[(position - 1) // SLOTS_PER_ROUND]
                record = slots[book_id][(position - 1) % SLOTS_PER_ROUND]
                method = self.config.method_of(book_id)
                forced = any(
                    self._injected(case, method, atom)
                    for case in (
                        self.spec.bank_fallback,
                        self.spec.book_fallback,
                        self.spec.archive_none,
                    )
                )
                start = rating_start + (position - 1) * RATING_SLOT_MS
                scores[book_id].append(self._rate(record, position, start, first, forced))
            self.t = rating_start + RATING_WINDOW_MS
            self._event("rating_window_end", atom_id=atom, round=round_, component="panel")
            for book in self.config.books:
                self._decide(book.book_id, atom, round_, first, slots, scores, incumbents)
            self._event("feedback_sent", atom_id=atom, round=round_, component="orchestrator")
            self.t += 200
            self._event("round_end", atom_id=atom, round=round_, component="orchestrator")
        self._event("atom_end", atom_id=atom, component="orchestrator")

    def _decide(
        self,
        book_id: str,
        atom: str,
        round_: int,
        first: bool,
        slots: dict[str, list[SlotRecord]],
        scores: dict[str, list[CandidateScore]],
        incumbents: dict[str, tuple[Fraction, int, SlotRecord] | None],
    ) -> None:
        before = incumbents[book_id]
        best = before
        by_id = {s.slot_id: s for s in slots[book_id]}
        for cand in sorted(scores[book_id], key=lambda c: c.slot_index):
            if not cand.eligible or cand.score is None:
                continue
            value = Fraction(cand.score)
            if best is None or value > best[0]:
                best = (value, cand.slot_index, by_id[cand.slot_id])
        incumbents[book_id] = best
        final = round_ == ROUNDS_PER_ATOM
        substituted = book_id in self.substituted
        if not final:
            action = "continue"
        elif substituted:
            action = "archive" if best else "archive_none"
        else:
            action = "commit" if best else "fallback_scan"
        self.write(
            DecisionRecord(
                run_id=self.run_id,
                batch_id=self.batch,
                book_id=book_id,
                atom_id=atom,
                round=round_,
                first_atom=first,
                candidates=tuple(sorted(scores[book_id], key=lambda c: c.slot_index)),
                incumbent_slot_id=best[2].slot_id if best else None,
                incumbent_score=_fraction_text(best[0]) if best else None,
                incumbent_changed=best is not before,
                final=final,
                action=action,  # type: ignore[arg-type]
                book_substituted=substituted,
                t_ms=self.t,
            )
        )
        if action == "commit" and best is not None:
            slot = best[2]
            assert slot.recipe is not None and slot.pcm_sha256 and slot.file_sha256
            self._commit(
                book_id,
                atom,
                dict(slot.recipe),
                slot.pcm_sha256,
                slot.file_sha256,
                source="selector",
                store_source=slot.slot_id,
                slot_id=slot.slot_id,
            )
        elif action == "fallback_scan":
            method = self.config.method_of(book_id)
            exhausted = self._injected(self.spec.book_fallback, method, atom)
            index = self._scan(book_id, atom, exhausted)
            if index is None:
                self._substitute(book_id, atom)
            else:
                entry = self.fallback.bank(self.config.profile)[index]
                self.used_bank[book_id].append(index)
                self._commit(
                    book_id,
                    atom,
                    entry.recipe.to_dict(),
                    entry.pcm_sha256,
                    entry.file_sha256,
                    source="fallback_bank",
                    store_source=entry.source,
                    bank_index=index,
                )

    # The run ------------------------------------------------------------------
    def run(self) -> None:
        books = self.books
        self._event("run_start", wall_utc=DEMO_UTC, component="orchestrator")
        self._startup(model_ms=95_000)
        self._span(
            "familiarization_start",
            "familiarization_end",
            600_000,
            book_id=books[Method.A1].book_id,
            actor_id=books[Method.A1].designer_id,
            component="a1",
        )
        self._event("operator_action", duration_ms=120_000, detail="panel setup")
        order = self.config.atom_order
        for appointment in range(1, len(order) // ATOMS_PER_APPOINTMENT + 1):
            if appointment == self.spec.resume_at:
                self.t = 0
                self._event("resume", component="orchestrator", detail="new process")
                self._startup(model_ms=90_000)
            self._event("appointment_start", appointment=appointment)
            atoms = order[
                (appointment - 1) * ATOMS_PER_APPOINTMENT : appointment * ATOMS_PER_APPOINTMENT
            ]
            for atom in atoms:
                self._atom(atom, first=atom == order[0])
                self.t += 30_000
            if appointment == 2:
                self._event(
                    "operator_action",
                    book_id=books[Method.A3].book_id,
                    duration_ms=45_000,
                    detail="restarted a service",
                )
                self._event(
                    "operator_action",
                    book_id=books[Method.A1].book_id,
                    duration_ms=30_000,
                    detail="station check",
                )
            if self.spec.incomplete and appointment == 2:
                rater = self.config.panel.raters[0]
                self._event("rater_withdrawal", actor_id=rater.rater_id, station=rater.station)
                self._event("batch_incomplete", detail="rater withdrew")
            self._event("appointment_end", appointment=appointment)
        self._event("run_end", wall_utc=CLOSED_UTC, component="orchestrator")

    def _startup(self, *, model_ms: int) -> None:
        a1 = self.books[Method.A1]
        self._span("startup_start", "startup_end", model_ms, component="llm")
        self._span("startup_start", "startup_end", 3_000, component="renderer")
        self._span("startup_start", "startup_end", 6_000, component="a1", actor_id=a1.designer_id)


def _fraction_text(value: Fraction) -> str:
    return (
        str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    )


def write_synthetic_batch(
    runs_root: str | Path, spec: SynthSpec | None = None, *, validate: bool = True
) -> RunLayout:
    """Write a synthetic batch run under `runs_root/<spec.run_id>` and return its layout.

    `validate=False` skips the per-record schema check (faster; the default spec is
    validated by the tests)."""
    spec = spec or SynthSpec()
    config = demo_config(spec.batch)
    layout = create_run_dir(runs_root, spec.run_id, RunKind.SYNTHETIC)
    config_sha = config.write(layout.config)
    synth = _Synth(spec, config, layout, validate=validate)
    synth.run()
    manifest = RunManifest(
        run_id=spec.run_id,
        kind=RunKind.SYNTHETIC,
        study=Study.A,
        purpose="test",
        clock="manual",
        created_utc=DEMO_UTC,
        code=SYNTH_CODE,
        threshold=config.threshold,
        config_sha256=config_sha,
        seed_namespace=config.seed_namespace,
        books=tuple(RunBook(b.book_id, b.method, b.designer_id) for b in config.books),
        closed_utc=CLOSED_UTC if spec.closed else None,
    )
    manifest.write(layout.manifest)
    return layout


def synth_spec(**changes: Any) -> SynthSpec:  # noqa: ANN401
    """`SynthSpec()` with `changes` (convenience for tests)."""
    return replace(SynthSpec(), **changes)


EXAMPLE_FILES: Final[tuple[str, ...]] = (
    "masked/books.csv",
    "masked/summary.json",
    "masked/summary.md",
    "unmasked/books.csv",
    "unmasked/summary.json",
    "unmasked/summary.md",
)
"""Audit outputs copied into the committed example (the per-book tables are listed in
`hashes.json` only)."""


EXAMPLE_DIR: Final = "runs/DEMO-AUDIT-01"
"""Where the committed DEMO summary lives, relative to `generation/` (architecture §8:
small summaries and hashes of DEMO runs go under `generation/runs/<run_id>/`)."""


def write_example(dest: str | Path, work: str | Path) -> dict[str, str]:
    """Rebuild the committed DEMO audit summary (`generation/runs/DEMO-AUDIT-01/`).

    Writes the synthetic batch under `work` (outside the repository), audits it, copies
    `EXAMPLE_FILES` and the tally sheet to `dest/audit/` and writes `dest/hashes.json`
    (the SHA-256 of every audit output, of the tally sheet and of every run file). The
    logs themselves are not kept (they are rebuilt bit for bit from the code). Returns the
    written files' SHA-256 by path relative to `dest`."""
    layout = write_synthetic_batch(Path(work) / "runs")
    out = Path(work) / "audit"
    result = build_audit(layout.root, out)
    target = Path(dest)
    written: dict[str, str] = {}
    for name in EXAMPLE_FILES:
        data = (out / name).read_bytes()
        (target / "audit" / name).parent.mkdir(parents=True, exist_ok=True)
        (target / "audit" / name).write_bytes(data)
        written[f"audit/{name}"] = hashlib.sha256(data).hexdigest()
    tally_sheet(layout.root, target / "audit" / "tally.csv")
    tally = hashlib.sha256((target / "audit" / "tally.csv").read_bytes()).hexdigest()
    written["audit/tally.csv"] = tally
    run_files = sorted(
        p.relative_to(layout.root).as_posix() for p in layout.root.rglob("*") if p.is_file()
    )
    hashes = {
        "description": (
            "Synthetic DEMO batch audit (#24): av_generation._audit_synth.SynthSpec() "
            "defaults, a log fixture (no component ran). Rebuild with `uv run --project "
            "generation python -m av_generation._audit_synth generation/runs/DEMO-AUDIT-01`."
        ),
        "audit": dict(sorted({**result.files, "tally.csv": tally}.items())),
        "run": {n: hashlib.sha256((layout.root / n).read_bytes()).hexdigest() for n in run_files},
    }
    data = document_text(hashes).encode("utf-8")
    (target / "hashes.json").write_bytes(data)
    written["hashes.json"] = hashlib.sha256(data).hexdigest()
    return written


def main(argv: list[str] | None = None) -> int:
    """`python -m av_generation._audit_synth <dest>`: rebuild the DEMO audit summary."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m av_generation._audit_synth <dest>", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as work:
        for name, digest in sorted(write_example(args[0], work).items()):
            print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
