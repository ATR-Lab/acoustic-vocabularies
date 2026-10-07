"""Interface contracts of #33 and #34 that the skeleton fixes (signatures, small types)."""

from __future__ import annotations

import inspect

import pytest

from av_analysis import missingness, simulate, unmask
from av_analysis.references import ExpectedHash, References
from av_analysis.scoring import BatteryScore

PCM = "1" * 64
FILE = "2" * 64


def test_expected_hash_matches_either_logged_kind():
    file_item = ExpectedHash("K-a1-r1", PCM, FILE)  # Study A trained message (WAV on disk)
    assert file_item.matches(FILE) and file_item.matches(PCM)
    assert not file_item.matches("3" * 64) and not file_item.matches("")
    composed = ExpectedHash("K-a1-r2", PCM, None)  # held-out or Study B message
    assert composed.matches(PCM) and not composed.matches(FILE)


def test_references_carry_both_hash_kinds_and_store_receipts():
    fields = set(inspect.signature(References).parameters)
    assert {"expected_hashes", "store_snapshots", "store_receipts", "active_person_id"} <= fields
    assert "active" not in fields


def test_issue_34_interfaces_take_the_inputs_they_need():
    for fn in (missingness.all_assigned_bounds, missingness.tipping_grid):
        params = list(inspect.signature(fn).parameters)
        assert params[:4] == ["study", "scores", "conditions", "planned"], fn.__name__
    assert "step" in inspect.signature(missingness.tipping_grid).parameters
    assert "operational_sum" in inspect.signature(BatteryScore).parameters
    assert {"tables", "files"} <= set(inspect.signature(simulate.SyntheticDataset).parameters)
    planned = inspect.signature(unmask.Conditions).parameters
    assert {"planned", "person_condition", "person_unit"} <= set(planned)


def test_interface_stubs_raise_not_implemented(tmp_path):
    with pytest.raises(NotImplementedError):
        missingness.all_assigned_bounds("A", [], None, {})  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        missingness.tipping_grid("A", [], None, {})  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        unmask.load_conditions(None, "A", "pilot")  # type: ignore[arg-type]
