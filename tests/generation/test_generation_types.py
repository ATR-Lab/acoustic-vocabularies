"""Proposer-facing data types (shared contracts).

Tests of owner schemas live in the owners' modules: `test_audit.py` (#24),
`test_freeze_manifest.py` (#25), `test_bank_manifest.py` (#26), `test_dry_run.py` (#22).
"""

from fractions import Fraction

import pytest
from av_sound import Profile, render
from av_sound.synthetic import synthetic_recipes

from av_generation import threshold
from av_generation.ids import Method, bank_slot_id
from av_generation.outcomes import SlotOutcome
from av_generation.proposers import (
    AtomFeedback,
    BCellState,
    BookState,
    CandidateFeedback,
    CommittedAtom,
    RetainedOption,
)


def test_book_state_references_and_feedback():
    recipes = synthetic_recipes(Profile.P1)
    committed = tuple(
        CommittedAtom(a, "X", recipes[a], render(recipes[a], "P1").pcm_sha256, i)
        for i, a in enumerate(("K-a1", "K-a2"))
    )
    book = BookState("DEMO-A-P01", "DEMO-BK-H9TC", Profile.P1, "0.10", committed)
    refs = book.references()
    assert [r.ref_id for r in refs] == ["K-a1", "K-a2"]
    assert len(committed[0].features) == 12
    cand = CandidateFeedback(
        "DEMO-BK-H9TC.K-a3.r1s2",
        1,
        2,
        2,
        recipes["K-a3"],
        SlotOutcome.VALID,
        (),
        (),
        True,
        Fraction(11, 2),
    )
    fb = AtomFeedback("DEMO-BK-H9TC", "K-a3", 1, (cand,), cand.slot_id, Fraction(11, 2))
    assert fb.incumbent() is cand
    assert AtomFeedback("DEMO-BK-H9TC", "K-a3", 0, (), None, None).incumbent() is None


def test_a2_gets_a_label_free_book_state():
    recipes = synthetic_recipes(Profile.P1)
    committed = tuple(
        CommittedAtom(a, label, recipes[a], render(recipes[a], "P1").pcm_sha256, i)
        for i, (a, label) in enumerate((("K-a1", "ADD_ONE"), ("Q-r2", "F")))
    )
    book = BookState("DEMO-A-P01", "DEMO-BK-M2RW", Profile.P1, "0.10", committed)
    free = book.without_labels()
    assert {a.semantic_label for a in free.committed} == {None}
    assert free.references() == book.references()
    sentinel = "ADD_ONE"
    assert sentinel not in repr(free) and sentinel in repr(book)


def test_b_cell_state_helpers():
    recipes = synthetic_recipes(Profile.P2)
    pcm = {a: render(recipes[a], "P2").pcm_sha256 for a in ("K-a1", "K-a2")}
    retained = (
        RetainedOption(
            Profile.P2,
            "K-a1",
            1,
            recipes["K-a1"],
            pcm["K-a1"],
            bank_slot_id("DEMO-bank-01", 1, "P2", "K-a1", 1),
        ),
        RetainedOption(
            Profile.P2,
            "K-a2",
            1,
            recipes["K-a2"],
            pcm["K-a2"],
            bank_slot_id("DEMO-bank-01", 1, "P2", "K-a2", 3),
        ),
    )
    cell = BCellState("DEMO-bank-01", 1, Profile.P2, "K-a2", 4, "TAG", retained, ())
    assert [r.ref_id for r in cell.other_atom_references()] == [retained[0].slot_id]
    assert cell.cell_hashes() == {pcm["K-a2"]}


def test_threshold_default_config():
    config = threshold.DEFAULT_CONFIG
    trials = len(config.profiles) * len(config.bin_centers) * config.pairs_per_bin
    assert trials + config.same_pairs == 224
    assert len(threshold.TRIAL_CSV_COLUMNS) == len(set(threshold.TRIAL_CSV_COLUMNS))


@pytest.mark.parametrize("method", list(Method))
def test_methods(method):
    assert method.value in ("A1", "A2", "A3", "B")
