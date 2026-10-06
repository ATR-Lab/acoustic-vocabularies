"""Study A batch configuration (`batch-config.schema.json`; restricted unless DEMO).

One file per batch run: the profile, the stored atom order and label permutation (from the
schedules batch table / `permutation.json`, #29), the three anonymous books and their
methods (from the restricted book key, #31), the panel's presentation order, the seed
namespace, the separation threshold and the fallback set the run uses. The orchestrator
(#20) reads it; the run manifest records its SHA-256 (`config_sha256`).

The file holds the method map, so it is restricted: only `DEMO-` configs enter git.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from av_sound.grammar import ATOM_IDS, parse_atom_id
from av_sound.recipe import Profile
from av_sound.store import SEMANTIC_LABELS

from av_generation.constants import PANEL_ORDERS
from av_generation.ids import STUDY_A_METHODS, Method, is_demo
from av_generation.records import Document, RecordError


@dataclass(frozen=True, slots=True)
class BookAssignment:
    """One method book of the batch."""

    book_id: str
    method: Method
    designer_id: str | None = None
    """A1 only: the anonymous designer ID (`D1`..`D3`)."""


@dataclass(frozen=True, slots=True)
class PanelAssignment:
    """The batch's rating panel and its presentation order of the three books."""

    panel_id: str
    order_index: int
    """1..6: index into `constants.PANEL_ORDERS` (method permutations)."""
    order: tuple[str, str, str]
    """Book IDs in rating order (blocks of three candidates follow this order)."""


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
