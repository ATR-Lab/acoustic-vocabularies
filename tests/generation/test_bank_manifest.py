"""Study B bank manifest and amendment log (#26 owns this module and both schemas).

Skeleton tests: the manifest schema (set/ID rules), the bank hash, the amendment chain
and the effective menu, plus the attempt rules #26 added to the schema. The typed
reader/writer, `verify` and `amend` live in `banks/` (`av_banks`) and are tested in
`tests/banks/` (they need the banks project).
"""

import copy

import pytest
from av_sound.dyad_bank import synthetic_dyad_bank
from av_sound.grammar import ATOM_IDS

from av_generation.bank_manifest import (
    BankSetError,
    amendment_chain_errors,
    bank_amendment_errors,
    bank_manifest_errors,
    bank_sha256,
    effective_menu,
    require_bank_set,
)
from av_generation.ids import bank_slot_id
from av_generation.jsonio import canonical_sha256

BANK = "DEMO-bank-01"


def _bank_manifest(bank_id: str = BANK, set_name: str = "demo") -> dict:
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
                        "option_id": f"{bank_id}.{profile}.{atom}.{opt.rank}",
                        "recipe": opt.recipe.to_dict(),
                        "recipe_sha256": opt.recipe.sha256(),
                        "pcm_sha256": opt.pcm_sha256,
                        "file_sha256": "c" * 64,
                        "wav": f"options/{profile}/{atom}-{opt.rank}.wav",
                        "slot_id": bank_slot_id(bank_id, 1, profile, atom, opt.rank),
                    }
                )
            cells.append({"profile": profile, "atom_id": atom, "slots_used": 4, "options": options})
    return {
        "format": "av-banks/bank-manifest",
        "format_version": 1,
        "bank_id": bank_id,
        "set": set_name,
        "demo": set_name == "demo",
        "dyad_slot": None,
        "bank_version": "0.1.0",
        "seed_namespace": bank_id,
        "generation_config_sha256": "d" * 64,
        "permutation_sha256": None,
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
    }


def test_bank_manifest_schema_and_hash():
    doc = _bank_manifest()
    assert bank_manifest_errors(doc) == ()
    assert bank_sha256(doc) == bank_sha256(copy.deepcopy(doc))
    bad = copy.deepcopy(doc)
    bad["cells"][0]["options"][3]["menu"] = "shown"
    assert bank_manifest_errors(bad)
    assert bank_manifest_errors(dict(doc, set="pilot"))
    assert bank_manifest_errors(dict(doc, status="unavailable"))
    assert bank_manifest_errors(dict(doc, amendments=[]))


@pytest.mark.parametrize(
    ("bank_id", "set_name", "ok"),
    [
        ("bank-P001", "pilot", True),
        ("bank-C072", "confirmatory", True),
        ("bank-C001", "pilot", False),
        ("bank-P003", "confirmatory", False),
        ("PILOT-B-01", "pilot", False),
        ("B-001", "confirmatory", False),
    ],
)
def test_set_and_bank_id_prefix_agree(bank_id, set_name, ok):
    doc = _bank_manifest(bank_id, set_name)
    assert (bank_manifest_errors(doc) == ()) is ok


def test_confirmatory_mode_refuses_pilot_and_demo_banks():
    require_bank_set(_bank_manifest("bank-C001", "confirmatory"), "confirmatory")
    for doc in (_bank_manifest("bank-P001", "pilot"), _bank_manifest()):
        with pytest.raises(BankSetError):
            require_bank_set(doc, "confirmatory")
    with pytest.raises(BankSetError):
        require_bank_set(dict(_bank_manifest(), bank_id="nope"), "demo")


def _amendment(manifest: dict, prev: str, seq: int = 1, rank: int = 2) -> dict:
    cell = manifest["cells"][5]
    return {
        "record": "bank_amendment",
        "record_version": 1,
        "bank_id": manifest["bank_id"],
        "bank_sha256": bank_sha256(manifest),
        "seq": seq,
        "prev_sha256": prev,
        "profile": cell["profile"],
        "atom_id": cell["atom_id"],
        "replaced_rank": rank,
        "replaced_option_id": cell["options"][rank - 1]["option_id"],
        "replacement_option_id": cell["options"][3]["option_id"],
        "recheck": {
            "ok": True,
            "pairs_checked": 60,
            "threshold": "0.10",
            "renderer_version": "0.1.0",
            "validator_version": "0.1.0",
        },
        "reason": "DEMO: option judged not audibly distinct",
        "date": "2026-11-05",
    }


def test_amendments_keep_the_bank_hash_and_change_the_effective_menu():
    doc = _bank_manifest()
    before = bank_sha256(doc)
    first = _amendment(doc, before)
    assert bank_amendment_errors(first) == ()
    assert amendment_chain_errors(doc, [first]) == ()
    assert bank_sha256(doc) == before
    menu = effective_menu(doc, [first])
    cell = doc["cells"][5]
    key = (cell["profile"], cell["atom_id"])
    ids = [o["option_id"] for o in cell["options"]]
    assert menu[key] == (ids[0], ids[3], ids[2])
    assert effective_menu(doc)[key] == tuple(ids[:3])
    assert len(menu) == 48


def test_amendment_chain_errors():
    doc = _bank_manifest()
    first = _amendment(doc, bank_sha256(doc))
    second = _amendment(doc, canonical_sha256(first), seq=2, rank=1)
    problems = amendment_chain_errors(doc, [first, second])
    assert any("already amended" in p for p in problems)
    assert amendment_chain_errors(doc, [_amendment(doc, "0" * 64)])
    assert amendment_chain_errors(doc, [dict(first, seq=2)])
    assert amendment_chain_errors(doc, [dict(first, replaced_option_id="x.y")])
    assert amendment_chain_errors(doc, [dict(first, replacement_option_id="x.y")])
    assert amendment_chain_errors(doc, [dict(first, bank_id="DEMO-bank-02")])
    assert amendment_chain_errors(doc, [dict(first, atom_id="Q-r9")])
    assert bank_amendment_errors(dict(first, recheck=dict(first["recheck"], ok=False)))
    with pytest.raises(ValueError):
        effective_menu(doc, [first, second])


def test_attempt_entries_name_the_failed_cell_only_when_failed():
    doc = _bank_manifest()
    failed = {
        "attempt": 1,
        "status": "failed",
        "slots_used": 31,
        "failed_cell": {"profile": "P1", "atom_id": "K-a3"},
        "slots_sha256": "f" * 64,
        "wall_ms": 900,
    }
    complete = dict(doc["attempts"][0], attempt=2)
    assert bank_manifest_errors(dict(doc, attempts=[failed, complete], attempt_used=2)) == ()
    for bad in (
        dict(failed, failed_cell=None),
        dict(complete, failed_cell={"profile": "P1", "atom_id": "K-a3"}),
        dict(complete, slots_used=191),
    ):
        assert bank_manifest_errors(dict(doc, attempts=[bad]))
    unavailable = dict(
        doc,
        status="unavailable",
        attempt_used=None,
        cells=[],
        attempts=[dict(failed, attempt=n) for n in (1, 2, 3, 4)],
    )
    assert bank_manifest_errors(unavailable) == ()
    assert bank_manifest_errors(dict(unavailable, attempts=unavailable["attempts"][:3]))
