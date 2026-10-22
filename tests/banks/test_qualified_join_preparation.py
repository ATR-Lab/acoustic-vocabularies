"""Reject invalid fixture authority before creating or changing any output."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from av_banks import handoff
from tools.prepare_joined_engineering import PreparationError
from tools.prepare_qualified_join_fixture import prepare


@pytest.mark.parametrize("bank_dir", ["../outside", "/outside", ".", "bank/../outside"])
def test_bank_directory_must_be_confined_before_output(tmp_path, bank_dir):
    root = tmp_path.resolve() / ".local"
    source = root / "source"
    source.mkdir(parents=True)
    (source / "fixture.json").write_text(
        json.dumps(dict(demo=True, participant_ready=False, bank_dir=bank_dir))
    )
    output = root / "output"
    with pytest.raises(PreparationError, match="RELATIVE_PATH"):
        prepare(source, output)
    assert not output.exists()


@pytest.mark.parametrize("bank_id,demo", [("DEMO-other", True), ("bank-C001", False)])
def test_index_cannot_register_a_different_or_real_bank(tmp_path, monkeypatch, bank_id, demo):
    root = tmp_path.resolve() / ".local"
    source = root / "source"
    source.mkdir(parents=True)
    (source / "fixture.json").write_text(
        json.dumps(
            dict(
                demo=True,
                participant_ready=False,
                bank_dir="bank",
                bank_id="DEMO-pinned",
                bank_sha256="a" * 64,
            )
        )
    )
    monkeypatch.setattr(
        handoff, "qualified_dyad_bank", lambda *a, **kw: SimpleNamespace(bank_id=bank_id, demo=demo)
    )
    output = root / "output"
    with pytest.raises(PreparationError, match="QUALIFIED_JOIN_BANK_ID"):
        prepare(source, output)
    assert not output.exists()
