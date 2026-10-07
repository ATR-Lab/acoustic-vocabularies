"""Frozen budgets, timings and decoding values of the generation system.

One place for every number the protocol fixes, so that proposers, the orchestrator, the
rater client, the audit and the G4 freeze manifest (#25) read the same values. Sources:
Study A protocol §3.1-§3.7 and Study B protocol §4. Changing a value here changes the
study configuration: it needs a protocol version and, after G4, a new freeze manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Final

from av_sound.composer import MAX_MESSAGE_SAMPLES, MIN_MESSAGE_SAMPLES
from av_sound.grammar import ATOM_IDS
from av_sound.tables import SAMPLES_PER_MS

# ---------------------------------------------------------------------------
# Study A budget (Study A protocol §3.3)

METHODS_A: Final[tuple[str, ...]] = ("A1", "A2", "A3")
"""Study A method labels. Restricted: never shown to raters or in masked outputs."""
ATOMS_PER_BOOK: Final = len(ATOM_IDS)
ROUNDS_PER_ATOM: Final = 4
SLOTS_PER_ROUND: Final = 3
SLOTS_PER_ATOM: Final = ROUNDS_PER_ATOM * SLOTS_PER_ROUND
"""The slot cap per atom per method (12); a 13th slot is refused."""
SLOTS_PER_BOOK: Final = ATOMS_PER_BOOK * SLOTS_PER_ATOM
SLOTS_PER_BATCH: Final = SLOTS_PER_BOOK * len(METHODS_A)
SLOT_CAP_MS: Final = 40_000
"""Proposal time per slot (all methods); at the cap the slot closes as `timeout`."""
PROPOSAL_WINDOW_MS: Final = SLOTS_PER_ROUND * SLOT_CAP_MS

RATERS_PER_PANEL: Final = 3
RATING_SLOT_MS: Final = 20_000
CANDIDATE_ONSET_MS: Final = 0
REFERENCE_ONSET_MS: Final = 2_000
RATING_SLOTS_PER_ROUND: Final = SLOTS_PER_ROUND * len(METHODS_A)
RATING_WINDOW_MS: Final = RATING_SLOTS_PER_ROUND * RATING_SLOT_MS
RATING_SLOTS_PER_RATER: Final = SLOTS_PER_BATCH
"""Rating-slot records per rater per batch, placeholders included (576)."""
RATING_MIN: Final = 1
RATING_MAX: Final = 7
COMFORT_VALUES: Final[tuple[str, ...]] = ("acceptable", "unacceptable")
MIN_ACCEPTABLE_COMFORT: Final = 2
"""Eligibility: technical checks pass and at least 2 of 3 raters mark comfort acceptable."""
FIRST_ATOM_DISTINGUISHABILITY: Final = 4
"""Distinguishability stored by rule for the first atom of a book (no reference)."""

ROUND_BUDGET_MS: Final = PROPOSAL_WINDOW_MS + RATING_WINDOW_MS
ATOM_BUDGET_MS: Final = ROUNDS_PER_ATOM * ROUND_BUDGET_MS
"""20 minutes per atom: the panel booking assumes it (§3.3)."""
ATOMS_PER_APPOINTMENT: Final = 4
APPOINTMENTS_PER_BATCH: Final = ATOMS_PER_BOOK // ATOMS_PER_APPOINTMENT
APPOINTMENT_BUDGET_MS: Final = ATOMS_PER_APPOINTMENT * ATOM_BUDGET_MS
APPOINTMENT_BOOKING_MS: Final = 90 * 60_000

PANEL_ORDERS: Final[tuple[tuple[str, str, str], ...]] = tuple(
    (a, b, c) for a, b, c in permutations(METHODS_A)
)
"""The 6 presentation orders of the three books, as method permutations in lexicographic
order; `order_index` 1..6 in a batch config indexes this tuple."""
PANELS_PER_SET: Final = 18
PANEL_ORDER_REPEATS: Final = PANELS_PER_SET // len(PANEL_ORDERS)

# ---------------------------------------------------------------------------
# Study B bank budget (Study B protocol §4)

PROFILES: Final[tuple[str, ...]] = ("P1", "P2", "P3")
B_SLOTS_PER_CELL: Final = 12
B_OPTIONS_PER_CELL: Final = 4
B_SHOWN_OPTIONS: Final = 3
B_CELLS: Final = ATOMS_PER_BOOK * len(PROFILES)
B_SLOTS_PER_ATTEMPT: Final = B_CELLS * B_SLOTS_PER_CELL
B_MAX_ATTEMPTS: Final = 4
B_MAX_SLOTS: Final = B_SLOTS_PER_ATTEMPT * B_MAX_ATTEMPTS

# ---------------------------------------------------------------------------
# A2 search (Study A protocol §3.5)

A2_PITCH_STEPS: Final[tuple[int, ...]] = (-3, -2, -1, 1, 2, 3)
A2_CHILD_MUTATIONS: Final[tuple[int, ...]] = (1, 2, 3)
"""Child k of rounds 2-4 mutates exactly k distinct coordinates."""

# ---------------------------------------------------------------------------
# A3 / B model and decoding (Study A protocol §3.6)

MODEL_ID: Final = "Qwen/Qwen2.5-7B-Instruct"
MODEL_REVISION: Final = "a09a35458c702b33eeacc393d103063234e8bc28"
"""Pinned Hugging Face revision; the LLM manifest (#16) is the authority and must match."""
MAX_INPUT_TOKENS: Final = 16_384
MIN_MAX_MODEL_LEN: Final = 16_896


@dataclass(frozen=True, slots=True)
class DecodingParams:
    """Frozen decoding values sent with every model call and logged on every request."""

    temperature: float
    top_p: float
    top_k: int
    repetition_penalty: float
    max_tokens: int


FROZEN_DECODING: Final = DecodingParams(
    temperature=0.7, top_p=0.9, top_k=50, repetition_penalty=1.0, max_tokens=512
)

# ---------------------------------------------------------------------------
# Complete-message duration bounds (renderer spec; audit #24)

MESSAGE_MIN_MS: Final = MIN_MESSAGE_SAMPLES // SAMPLES_PER_MS
MESSAGE_MAX_MS: Final = MAX_MESSAGE_SAMPLES // SAMPLES_PER_MS
