"""A1 hand-designer service and web interface (#19). INTERFACE ONLY in the skeleton.

The orchestrator (#20) calls `A1SlotService.propose_round(request)`, which opens the
round's proposal window for the designer's book and blocks until three slots have
closed. Opening a slot calls `SlotLedger.reserve` (a 13th request is refused and logged
before anything is designed or heard) and starts a server-side 40-s timer; submit
validates, renders and consumes the slot, then hands out a single-use audio token if the
recipe is valid. Slots the designer never opens close as `timeout` when the window ends.
The screen shows the atom's meaning from the shared meaning set (`meanings`). A second
play of a token is refused and logged (`records.PlayEvent`, `result="refused"`). A slot
never reopens; a submitted recipe cannot be edited.

Web stack (all issues): FastAPI app + plain HTML/CSS/JS served from
`av_generation/web/a1/` (no npm build, no CDN, no third-party JS). Practice mode writes
to a separate `practice` run and never to a book.

Route contract (paths are fixed here so the dry-run bot designer (#22) can drive them):
"""

from __future__ import annotations

from typing import Any, Final

from av_generation.clock import Clock
from av_generation.ids import Method
from av_generation.ledger import SlotLedger
from av_generation.meanings import MeaningSet
from av_generation.proposers import RoundRequest, RoundResult
from av_generation.records import RecordWriter

ROUTES: Final[dict[str, str]] = {
    "page": "GET /a1/",
    "state": "GET /a1/api/state",
    "open_slot": "POST /a1/api/slots/open",
    "submit": "POST /a1/api/slots/{slot_id}/submit",
    "audio": "GET /a1/api/audio/{token}",
    "feedback": "GET /a1/api/feedback",
    "book": "GET /a1/api/book",
    "activity": "POST /a1/api/activity",
}
"""A1 HTTP routes (#19 fills request/response bodies in docs/interfaces/generation.md)."""


class A1SlotService:
    """`proposers.RoundProposer` for A1 plus the state behind the web app (#19)."""

    method = Method.A1

    def __init__(
        self,
        ledger: SlotLedger,
        plays: RecordWriter,
        timing: RecordWriter,
        *,
        clock: Clock,
        designer_id: str,
        meanings: MeaningSet,
        practice: bool = False,
    ) -> None:
        raise NotImplementedError("#19: A1 slot service")

    def propose_round(self, request: RoundRequest) -> RoundResult:
        raise NotImplementedError("#19: A1 slot service")


def create_a1_app(service: A1SlotService) -> Any:  # noqa: ANN401 - FastAPI app
    """The FastAPI app serving `ROUTES` (#19)."""
    raise NotImplementedError("#19: A1 web app")
