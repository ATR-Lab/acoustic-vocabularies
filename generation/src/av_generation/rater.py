"""Bot rater for synthetic panels (#21 implements; #22 uses). INTERFACE ONLY.

The human station is the static page of `av_generation.panel` (#21). `BotRater` is a
station driven by code that speaks exactly `av_generation.rater_protocol` over a
WebSocket: `hello` with `kind="bot"`, clock sync, preload with hash check, `played` at
the scheduled onsets (no audio output), and one `rating` per rateable slot drawn from
`seeds.rng_for(seeds.bot_seed_key(run_id, rater_id, "rating", rating_slot_id))`.

It sees only what a station sees: rating-slot IDs, positions, meanings and asset IDs,
never a book ID or method. Fallback injection (#22) is therefore keyed by rating-slot
ID: the driver computes the IDs from the restricted batch config
(`BatchConfig.rating_slot_ids(book_id, atom_id)`) and passes them in
`BotRatingPolicy.force_unacceptable_slots`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BotRatingPolicy:
    """Synthetic rating distribution (#22 proposed: comfort acceptable with p = 0.9;
    association and distinguishability uniform on 1..7)."""

    p_comfort_acceptable: float = 0.9
    force_unacceptable_slots: frozenset[str] = field(default_factory=frozenset)
    """Rating-slot IDs every bot rates comfort `unacceptable` (zero-eligible injection)."""
    p_missing: float = 0.0
    """Probability of submitting no rating in a slot (missing-rating fixtures)."""


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
