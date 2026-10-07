"""Round orchestrator, panel session host and commit/fallback (#20). INTERFACE ONLY.

Per atom (in `BatchConfig.atom_order`), for rounds 1..4: open the three proposal windows
in parallel (`proposers.RoundProposer`, at most 3 x 40 s), then play the 9 rating slots
in the panel's book order (`BatchConfig.panel.order`, blocks of three candidates in slot
order), then compute the selector state and return each book's own feedback. After round
4 commit the incumbent, or scan the fallback bank, or substitute the whole fallback
book. State is persisted after every slot, so a batch pauses between atoms and resumes.

Before the first atom: `genconfig.check_run_config(generation_config, kind=...,
freeze_manifest=...)` (confirmatory batches refuse to start unless the frozen config hash
matches), and `generation_config.separation_threshold == config.threshold`. On open and
on `resume`, every log is repaired with `jsonio.repair_torn_tail` (logged as a
`log_repaired` timing event).

Proposer inputs: A2 gets `BookState.without_labels()` and `semantic_label=None`; A1 and A3
get the atom's meaning label (texts from `meanings`). Stations show meaning texts and
never a book name; the operator console shows panel aliases
(`BatchConfig.panel.aliases`) only, never book IDs.

Whole-book substitution (architecture §3.2; Study A protocol §3.7). When the bank scan of
atom k of book X finds nothing: void X's store book (`cause="failed_generation"`), commit
the 16 atoms of the profile's frozen fallback book to a new store book (16 `commit`
records, `source="fallback_book"`, `failed_generation=true`), and log a
`book_substituted` timing event. The method then keeps proposing, and its candidates keep
being rated, for atoms k+1..16 with unchanged budget and panel schedule: its `BookState`
is the continued book (atoms committed before k plus the incumbents archived since),
round-4 decisions are `archive` / `archive_none` with `book_substituted=true`, nothing is
committed and no further scan runs. So a batch always has 576 slot records, 576 rating
records per rater, 192 decisions and one commit per atom in each book's final store book
(48); a book substituted at atom k adds its k-1 superseded commits.

Logs (`rundir.LOG_FILES`): `decision`, `commit`, `fallback_scan`, `timing`, and, as the
panel session host (`panel_session`), `rating`, `play` and the panel timing events;
`slot` records come from the proposers' ledger.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from av_sound.fallback import FallbackSet
from av_sound.store import VocabularyStore

from av_generation.clock import Clock
from av_generation.config import BatchConfig
from av_generation.genconfig import GenerationConfig
from av_generation.ids import Method, RunKind
from av_generation.meanings import MeaningSet
from av_generation.panel_session import PanelSessionHost, PanelSlot
from av_generation.proposers import RoundProposer
from av_generation.rundir import RunLayout


@dataclass(frozen=True, slots=True)
class RatingSlotPlan:
    """One scheduled rating slot, server side (#20 internal; never sent to a station).

    `panel` is the part the panel server sees; the rest identifies the candidate."""

    panel: PanelSlot
    batch_id: str
    book_id: str
    alias: str
    """The book's panel alias (operator console)."""
    slot_id: str
    atom_id: str
    round: int


class Orchestrator:
    """Runs one Study A batch (#20)."""

    def __init__(
        self,
        config: BatchConfig,
        layout: RunLayout,
        proposers: Mapping[Method, RoundProposer],
        store: VocabularyStore,
        fallback: FallbackSet,
        *,
        clock: Clock,
        generation_config: GenerationConfig,
        meanings: MeaningSet,
        kind: RunKind,
        freeze_manifest: Mapping[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError("#20: orchestrator")

    def run_atom(self, atom_id: str) -> None:
        """Run 4 rounds for one atom in every book and commit (or fall back)."""
        raise NotImplementedError("#20: orchestrator")

    def run_appointment(self, appointment: int) -> None:
        """Run the 4 atoms of appointment 1..4."""
        raise NotImplementedError("#20: orchestrator")

    def resume(self) -> None:
        """Repair torn log tails, rebuild state from the run's logs and continue at the
        next atom."""
        raise NotImplementedError("#20: orchestrator")

    def panel_host(self) -> PanelSessionHost:
        """The session API for the panel server (`panel.create_panel_app`, #21)."""
        raise NotImplementedError("#20: orchestrator")


def panel_order_schedule(set_ns: str, panel_ids: Sequence[str]) -> Mapping[str, int]:
    """Panel ID -> order index 1..6, each order exactly 3 times over 18 panels (stored
    seed `seeds.panel_seed_key(set_ns, "orders")`)."""
    raise NotImplementedError("#20: panel order schedule")


def panel_aliases(set_ns: str, panel_id: str, book_ids: Sequence[str]) -> Mapping[str, str]:
    """Book ID -> distinct panel alias (`ids.PANEL_ALIAS_RE`) for one panel, drawn with
    `seeds.rng_for(seeds.panel_seed_key(set_ns, "aliases", panel_id))`; stored in
    `BatchConfig.panel.aliases`."""
    raise NotImplementedError("#20: panel aliases")
