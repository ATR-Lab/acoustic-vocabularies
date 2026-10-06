"""Proposer-facing data types, audit/freeze contracts and the bank manifest schema."""

import copy
from fractions import Fraction

import pytest
from av_sound import Profile, render
from av_sound.dyad_bank import synthetic_dyad_bank
from av_sound.grammar import ATOM_IDS
from av_sound.synthetic import synthetic_recipes

from av_generation import audit, freeze, threshold
from av_generation._schemas import schema_errors
from av_generation.bank_manifest import bank_manifest_errors, bank_sha256
from av_generation.ids import Method, bank_slot_id
from av_generation.outcomes import OUTCOME_CODES, SlotOutcome
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


def test_audit_columns():
    assert tuple(f"n_{c}" for c in OUTCOME_CODES) == audit.OUTCOME_COLUMNS
    assert len(set(audit.BOOK_COLUMNS)) == len(audit.BOOK_COLUMNS)
    assert set(audit.BOOK_COLUMNS) >= audit.METHOD_COLUMNS
    assert not set(audit.MASKED_BOOK_COLUMNS) & audit.METHOD_COLUMNS


def _book_row(masked: bool) -> dict:
    row = {c: 0 for c in audit.BOOK_COLUMNS}
    row.update(
        batch_id="DEMO-A-P01",
        book_id="DEMO-BK-H9TC",
        profile="P1",
        failed_generation=False,
        nonfallback=True,
        method="A3",
        designer_id=None,
        candidate_diversity=0.31,
        committed_diversity=None,
        wall_ms=None,
    )
    if masked:
        row = {k: v for k, v in row.items() if k not in audit.METHOD_COLUMNS}
    return row


def _summary(masked: bool, row: dict) -> dict:
    return {
        "format": "av-generation/audit-summary",
        "format_version": 1,
        "masked": masked,
        "run_id": "DEMO-run-01",
        "batch_id": "DEMO-A-P01",
        "sources": {"logs/slots.jsonl": "a" * 64},
        "books": [row],
        "timing": {
            "batch_wall_ms": None,
            "appointments": [],
            "atoms": [],
            "max_atom_ms": None,
            "max_appointment_ms": None,
        },
    }


def test_audit_summary_masking_rule():
    assert schema_errors("audit-summary.schema.json", _summary(False, _book_row(False))) == ()
    assert schema_errors("audit-summary.schema.json", _summary(True, _book_row(True))) == ()
    assert schema_errors("audit-summary.schema.json", _summary(True, _book_row(False)))
    assert schema_errors("audit-summary.schema.json", _summary(False, _book_row(True)))
    assert tuple(_book_row(False)) == audit.BOOK_COLUMNS


def test_freeze_manifest_schema():
    items = [
        {"key": k, "category": k.split(".")[0], "value": None, "sha256": None, "source": "pending"}
        for k in freeze.REQUIRED_ITEM_KEYS
    ]
    doc = {
        "format": freeze.FREEZE_FORMAT,
        "format_version": 1,
        "freeze_version": "1.0",
        "status": "draft",
        "protocol_version": "0.1",
        "repo_commit": None,
        "tag": None,
        "items": items,
        "apparatus": {f: None for f in freeze.APPARATUS_FIELDS},
        "signoff": [],
    }
    assert schema_errors("freeze-manifest.schema.json", doc) == ()
    assert schema_errors("freeze-manifest.schema.json", dict(doc, status="frozen"))
    assert len(set(freeze.REQUIRED_ITEM_KEYS)) == len(freeze.REQUIRED_ITEM_KEYS)


def _bank_manifest() -> dict:
    bank = synthetic_dyad_bank("DEMO-bank-01")
    cells = []
    for profile in ("P1", "P2", "P3"):
        for atom in ATOM_IDS:
            options = []
            for opt in bank.cells[(profile, atom)]:
                options.append(
                    {
                        "rank": opt.rank,
                        "menu": opt.menu,
                        "option_id": f"DEMO-bank-01.{profile}.{atom}.{opt.rank}",
                        "recipe": opt.recipe.to_dict(),
                        "recipe_sha256": opt.recipe.sha256(),
                        "pcm_sha256": opt.pcm_sha256,
                        "file_sha256": "c" * 64,
                        "wav": f"options/{profile}/{atom}-{opt.rank}.wav",
                        "slot_id": bank_slot_id("DEMO-bank-01", 1, profile, atom, opt.rank),
                    }
                )
            cells.append({"profile": profile, "atom_id": atom, "slots_used": 4, "options": options})
    return {
        "format": "av-banks/bank-manifest",
        "format_version": 1,
        "bank_id": "DEMO-bank-01",
        "set": "demo",
        "demo": True,
        "dyad_slot": None,
        "bank_version": "0.1.0",
        "config_sha256": "d" * 64,
        "status": "complete",
        "attempt_used": 1,
        "attempts": [
            {
                "attempt": 1,
                "status": "complete",
                "slots_used": 192,
                "failed_cell": None,
                "slots_sha256": "e" * 64,
                "wall_ms": 1000,
            }
        ],
        "labels": dict(bank.labels),
        "atom_order": list(ATOM_IDS),
        "cells": cells,
        "amendments": [],
    }


def test_bank_manifest_schema_and_hash():
    doc = _bank_manifest()
    assert bank_manifest_errors(doc) == ()
    assert bank_sha256(doc) == bank_sha256(copy.deepcopy(doc))
    bad = copy.deepcopy(doc)
    bad["cells"][0]["options"][3]["menu"] = "shown"
    assert bank_manifest_errors(bad)
    pilot_as_demo = dict(doc, set="pilot")
    assert bank_manifest_errors(pilot_as_demo)
    unavailable = dict(doc, status="unavailable")
    assert bank_manifest_errors(unavailable)


def test_threshold_default_config():
    config = threshold.DEFAULT_CONFIG
    trials = len(config.profiles) * len(config.bin_centers) * config.pairs_per_bin
    assert trials + config.same_pairs == 224
    assert len(threshold.TRIAL_CSV_COLUMNS) == len(set(threshold.TRIAL_CSV_COLUMNS))


@pytest.mark.parametrize("method", list(Method))
def test_methods(method):
    assert method.value in ("A1", "A2", "A3", "B")
