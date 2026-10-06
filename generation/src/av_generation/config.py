"""Study A batch configuration (`batch-config.schema.json`; restricted unless DEMO).

One file per batch run: the profile, the stored atom order and label permutation (from the
schedules batch table / `permutation.json`, #29), the three anonymous books and their
methods (from the restricted book key, #31), the panel (raters, presentation order and
the per-panel book aliases shown on the operator console), the seed namespace, the
separation threshold and the fallback set the run uses. The orchestrator (#20) reads it;
the run manifest records its SHA-256 (`config_sha256`).

`rating_positions(book_id)` and `rating_slot_ids(book_id, atom_id)` give the rating
slots of one book's candidates: the #22 driver uses them to tell bot raters which
rating slots to rate unacceptable (fallback injection) without any book ID reaching a
station.

The file holds the method map, so it is restricted: only `DEMO-` configs enter git.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from av_sound.grammar import ATOM_IDS, parse_atom_id
from av_sound.recipe import Profile
from av_sound.store import SEMANTIC_LABELS

from av_generation.constants import PANEL_ORDERS, RATERS_PER_PANEL, ROUNDS_PER_ATOM, SLOTS_PER_ROUND
from av_generation.ids import (
    PANEL_ALIAS_RE,
    STUDY_A_METHODS,
    Method,
    check_atom,
    is_demo,
    rating_slot_id,
)
from av_generation.records import Document, RecordError


@dataclass(frozen=True, slots=True)
class BookAssignment:
    """One method book of the batch."""

    book_id: str
    method: Method
    designer_id: str | None = None
    """A1 only: the anonymous designer ID (`D1`..`D3`)."""


@dataclass(frozen=True, slots=True)
class RaterSeat:
    """One rater of the panel: the coded rater ID, the station and whether a bot sits
    there (synthetic runs). The panel session host (#20) accepts `hello` only from these
    seats and writes 9 rating records per seat per round (`panel_session`)."""

    rater_id: str
    station: str
    kind: Literal["human", "bot"]


@dataclass(frozen=True, slots=True)
class PanelAssignment:
    """The batch's rating panel, its presentation order of the three books and the
    per-panel aliases of the books (Study A protocol §3.1: book IDs rotate between panels)."""

    panel_id: str
    order_index: int
    """1..6: index into `constants.PANEL_ORDERS` (method permutations)."""
    order: tuple[str, str, str]
    """Book IDs in rating order (blocks of three candidates follow this order)."""
    aliases: Mapping[str, str]
    """Book ID -> panel alias (`ids.PANEL_ALIAS_RE`), drawn per panel with
    `seeds.panel_seed_key(set_ns, "aliases", panel_id)` (#20). The operator console shows
    aliases only."""
    raters: tuple[RaterSeat, ...]
    """The three seats, in station order."""


@dataclass(frozen=True, slots=True)
class ConfigSources:
    """SHA-256 of the schedules files the config was derived from (provenance)."""

    batch_table_sha256: str | None = None
    book_key_sha256: str | None = None
    permutation_sha256: str | None = None
    panel_schedule_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class BatchConfig(Document):
    """A Study A batch (#20). `check_consistency()` adds the cross-field rules."""

    TAG = "av-generation/batch-config"
    VERSION = 1
    SCHEMA = "batch-config.schema.json"

    batch_id: str
    set: Literal["demo", "pilot", "confirmatory"]
    profile: Profile
    atom_order: tuple[str, ...]
    """The 16 atom IDs in generation/commit order (`permutation.json` `atom_order`)."""
    labels: Mapping[str, str]
    """Atom ID -> semantic label (the batch's permutation)."""
    books: tuple[BookAssignment, ...]
    panel: PanelAssignment
    seed_namespace: str
    """Part `<batch_ns>` of the A1/A2/A3 seed keys (a rebuild uses a new namespace)."""
    threshold: str
    fallback_manifest_sha256: str
    fallback_bank_hash: str
    sources: ConfigSources

    def check_consistency(self) -> BatchConfig:
        """Cross-field rules the schema cannot express; raises `RecordError`."""
        problems: list[str] = []
        if sorted(self.atom_order) != sorted(ATOM_IDS) or len(self.atom_order) != len(ATOM_IDS):
            problems.append("atom_order must be a permutation of the 16 atom IDs")
        if set(self.labels) != set(ATOM_IDS):
            problems.append("labels must cover the 16 atom IDs")
        else:
            for atom, label in self.labels.items():
                ref = parse_atom_id(atom)
                if label not in SEMANTIC_LABELS[(ref.family, ref.role)]:
                    problems.append(f"{atom}: {label!r} is not a {ref.family} {ref.role} label")
            for key, allowed in SEMANTIC_LABELS.items():
                used = sorted(
                    lab
                    for a, lab in self.labels.items()
                    if (parse_atom_id(a).family, parse_atom_id(a).role) == key
                )
                if used != sorted(allowed):
                    problems.append(f"labels of {key[0]} {key[1]} are not a permutation")
        methods = [b.method for b in self.books]
        if sorted(methods) != sorted(STUDY_A_METHODS):
            problems.append("books must hold exactly one A1, one A2 and one A3 book")
        for book in self.books:
            if (book.method is Method.A1) != (book.designer_id is not None):
                problems.append(f"{book.book_id}: designer_id is set for A1 books only")
        by_id = {b.book_id: b.method for b in self.books}
        if len(by_id) != len(self.books):
            problems.append("book IDs must be distinct")
        if set(self.panel.aliases) != set(by_id):
            problems.append("panel.aliases must name the three book IDs")
        aliases = list(self.panel.aliases.values())
        if len(set(aliases)) != len(aliases) or not all(
            PANEL_ALIAS_RE.fullmatch(a) for a in aliases
        ):
            problems.append("panel aliases must be distinct PB-XXXX aliases")
        seats = self.panel.raters
        if (
            len(seats) != RATERS_PER_PANEL
            or len({s.rater_id for s in seats}) != len(seats)
            or len({s.station for s in seats}) != len(seats)
        ):
            problems.append(f"panel.raters must be {RATERS_PER_PANEL} distinct raters and stations")
        if self.set != "demo" and any(s.kind == "bot" for s in seats):
            problems.append("bot raters sit only in demo (synthetic) batches")
        if sorted(self.panel.order) != sorted(by_id):
            problems.append("panel.order must list the three book IDs")
        elif (
            not 1 <= self.panel.order_index <= len(PANEL_ORDERS)
            or tuple(by_id[b].value for b in self.panel.order)
            != PANEL_ORDERS[self.panel.order_index - 1]
        ):
            problems.append("panel.order does not match panel.order_index")
        demo_ids = [is_demo(self.batch_id), *(is_demo(b.book_id) for b in self.books)]
        if (self.set == "demo") != all(demo_ids) or (self.set != "demo" and any(demo_ids)):
            problems.append("demo configs, and only they, use DEMO- batch and book IDs")
        if problems:
            raise RecordError("inconsistent batch config", tuple(problems))
        return self

    def rating_positions(self, book_id: str) -> tuple[int, ...]:
        """Play positions 1..9 of a book's three candidates in every round (its block of
        `panel.order`, slot order within the block)."""
        try:
            block = self.panel.order.index(book_id)
        except ValueError:
            raise KeyError(book_id) from None
        return tuple(block * SLOTS_PER_ROUND + slot for slot in range(1, SLOTS_PER_ROUND + 1))

    def rating_slot_ids(self, book_id: str, atom_id: str) -> tuple[str, ...]:
        """The 12 rating-slot IDs of one book's candidates for one atom (rounds 1..4)."""
        check_atom(atom_id)
        positions = self.rating_positions(book_id)
        return tuple(
            rating_slot_id(self.batch_id, atom_id, round_, position)
            for round_ in range(1, ROUNDS_PER_ATOM + 1)
            for position in positions
        )

    def method_of(self, book_id: str) -> Method:
        """The method of a book (restricted information)."""
        for book in self.books:
            if book.book_id == book_id:
                return book.method
        raise KeyError(book_id)

    def book_of(self, method: Method | str) -> BookAssignment:
        """The book of a method."""
        for book in self.books:
            if book.method is Method(method):
                return book
        raise KeyError(method)
