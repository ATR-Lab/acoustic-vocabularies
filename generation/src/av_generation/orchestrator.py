"""Round orchestrator, panel session and commit/fallback (#20). INTERFACE ONLY.

Per atom (in `BatchConfig.atom_order`), for rounds 1..4: open the three proposal windows
in parallel (`proposers.RoundProposer`, at most 3 x 40 s), then play the 9 rating slots
in the panel's book order (`BatchConfig.panel.order`, blocks of three candidates in slot
order), then compute the selector state and return each book's own feedback. After round
4 commit the incumbent, or scan the fallback bank, or substitute the whole fallback
book. State is persisted after every slot, so a batch pauses between atoms and resumes.

Logs (`rundir.LOG_FILES`): `decision`, `commit`, `fallback_scan`, `timing`; `rating`
records come from the panel session; `slot` records from the proposers' ledger.
The rater protocol is `av_generation.rater_protocol`; the panel session serves it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from av_sound.fallback import FallbackSet
from av_sound.store import VocabularyStore

from av_generation.clock import Clock
from av_generation.config import BatchConfig
from av_generation.ids import Method
from av_generation.proposers import RoundProposer
from av_generation.records import PlayEvent, RatingRecord
from av_generation.rundir import RunLayout


@dataclass(frozen=True, slots=True)
class AssetRef:
    """A WAV the panel serves: `asset_id` is the file SHA-256 (`rater_protocol`)."""

    asset_id: str
    pcm_sha256: str
    n_samples: int


@dataclass(frozen=True, slots=True)
class RatingSlotPlan:
    """One scheduled rating slot (server side; the station sees a `slot` message)."""

    rating_slot_id: str
    batch_id: str
    book_id: str
    slot_id: str
    atom_id: str
    round: int
    position: int
    start_ms: int
    placeholder: bool
    first_atom: bool
    meaning: str | None
    candidate: AssetRef | None
    reference: AssetRef | None
    reference_meaning: str | None


class PanelSessionHost(Protocol):
    """What the rater panel server (#21 client side) needs from the orchestrator (#20)."""

    def current_slot(self) -> RatingSlotPlan | None:
        """The slot being played now (for reconnects), or `None`."""
        ...

    def upcoming_assets(self) -> Sequence[AssetRef]:
        """Assets of the next round, for preloading."""
        ...

    def asset_bytes(self, asset_id: str) -> bytes:
        """Canonical WAV bytes of an asset."""
        ...

    def submit_rating(self, record: RatingRecord) -> None:
        """Store one rating record (validated, locked after 20 s)."""
        ...

    def report_play(self, event: PlayEvent) -> None:
        """Log a play reported by a station."""
        ...

    def report_withdrawal(self, rater_id: str, station: str) -> None:
        """A rater withdrew: keep records, mark the batch incomplete."""
        ...


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
    ) -> None:
        raise NotImplementedError("#20: orchestrator")

    def run_atom(self, atom_id: str) -> None:
        """Run 4 rounds for one atom in every book and commit (or fall back)."""
        raise NotImplementedError("#20: orchestrator")

    def run_appointment(self, appointment: int) -> None:
        """Run the 4 atoms of appointment 1..4."""
        raise NotImplementedError("#20: orchestrator")

    def resume(self) -> None:
        """Rebuild state from the run's logs and continue at the next atom."""
        raise NotImplementedError("#20: orchestrator")

    def panel_host(self) -> PanelSessionHost:
        """The session API for the rater stations."""
        raise NotImplementedError("#20: orchestrator")


def create_panel_app(host: PanelSessionHost) -> Any:  # noqa: ANN401 - FastAPI app
    """FastAPI app serving `rater_protocol.WS_PATH`, `ASSET_PATH` and `STATION_PAGE`."""
    raise NotImplementedError("#20/#21: panel server")


def panel_order_schedule(set_ns: str, panel_ids: Sequence[str]) -> Mapping[str, int]:
    """Panel ID -> order index 1..6, each order exactly 3 times over 18 panels (stored
    seed `seeds.panel_seed_key(set_ns, "orders")`)."""
    raise NotImplementedError("#20: panel order schedule")
