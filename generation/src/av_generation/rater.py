"""Rater stations: browser client and bot rater (#21). INTERFACE ONLY in the skeleton.

The station page (`av_generation/web/rater/`, plain HTML/JS) speaks
`av_generation.rater_protocol`: it preloads and hash-checks every asset, syncs to the
server clock, plays the candidate at 0 s and the reference at 2 s exactly once, unlocks
the controls after the reference (or at 2 s), locks them at 20 s and sends one `rating`.
There is no replay, seek or volume control. `BotRater` (#22) speaks the same protocol
over a WebSocket with seeded synthetic ratings.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BotRatingPolicy:
    """Synthetic rating distribution (#22 proposed: comfort acceptable with p = 0.9;
    association and distinguishability uniform on 1..7)."""

    p_comfort_acceptable: float = 0.9
    force_unacceptable_atoms: tuple[str, ...] = ()
    """Atom IDs (with book IDs, as `book.atom`) rated unacceptable by every bot (fallback
    injection)."""


class BotRater:
    """A rater station driven by code (#21 implements; #22 uses)."""

    def __init__(
        self,
        base_url: str,
        *,
        rater_id: str,
        station: str,
        run_id: str,
        policy: BotRatingPolicy,
    ) -> None:
        raise NotImplementedError("#21: bot rater")

    def run(self) -> None:
        """Connect, follow the server's slots until `end`, rate each rateable slot."""
        raise NotImplementedError("#21: bot rater")
