"""#6 integration chain: one SYNTHETIC root through reconciliation, dashboard and analysis."""

from __future__ import annotations

import csv
import shutil

import pytest

from av_analysis.cli import main
from av_analysis.paths import DataRoot, WatermarkError
from av_analysis.synthetic_logs import unseal_synthetic_keys

SEED = "DEMO-test-o4.5-chain"
KEY = "keys/A/pilot-book-key.json"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("chain") / "root"
    # One whole unit per study: the Study A analysis needs every method of a batch.
    args = ["synth-logs", "--demo-seed", SEED, "--out", str(out)]
    assert main(args) == 0
    return out


@pytest.fixture
def root(built, tmp_path):
    target = tmp_path / "root"
    shutil.copytree(built, target)
    return target


def rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_synth_logs_writes_no_keys(built):
    assert not (built / "keys").exists() or not any((built / "keys").rglob("*.json"))


def test_full_chain_on_one_root(root):
    assert main(["refresh", "--root", str(root)]) == 0
    visits = rows(root / "reconciled" / "visit-status.csv")
    assert {v["study"] for v in visits} == {"A", "B"}
    assert all(v["reconciliation"] == "pass" for v in visits)
    assert rows(root / "reconciled" / "discrepancies.csv") == []
    assert (root / "monitoring" / "index.html").is_file()
    # Masked until unblinding: Study A analysis refuses without the book key.
    assert main(["run", "--study", "A", "--data", str(root), "--glmm", "skip"]) == 2
    assert main(["synth-logs", "--demo-seed", SEED, "--out", str(root), "--unseal-keys"]) == 0
    assert (root / KEY).is_file()
    for study in ("A", "B"):
        assert main(["run", "--study", study, "--data", str(root), "--glmm", "skip"]) == 0
        assert (root / "estimates" / f"report-{study}.md").is_file()


def test_unseal_is_idempotent_and_refuses_a_changed_key(root):
    data_root = DataRoot.open(root)
    assert unseal_synthetic_keys(data_root) == ["A/pilot-book-key.json"]
    first = (root / KEY).read_bytes()
    assert unseal_synthetic_keys(data_root) == []
    assert (root / KEY).read_bytes() == first
    (root / KEY).chmod(0o644)
    (root / KEY).write_bytes(first + b" ")
    with pytest.raises(WatermarkError, match="differs"):
        unseal_synthetic_keys(data_root)


def test_unseal_refuses_another_label_and_a_missing_root(root, tmp_path, capsys):
    args = ["synth-logs", "--demo-seed", "DEMO-other", "--out", str(root), "--unseal-keys"]
    assert main(args) == 2
    assert "not the root's label" in capsys.readouterr().err
    args = ["synth-logs", "--demo-seed", SEED, "--out", str(tmp_path / "none"), "--unseal-keys"]
    assert main(args) == 2


def test_unseal_writes_nothing_for_a_study_b_root(tmp_path):
    data_root = DataRoot.create(
        tmp_path / "b", "SYNTHETIC", study="B", set_name="pilot", label=SEED
    )
    assert unseal_synthetic_keys(data_root) == []


def test_unseal_refuses_a_real_root(tmp_path):
    data_root = DataRoot.create(tmp_path / "r", "REAL", study="A", set_name="pilot", label="REAL-x")
    with pytest.raises(WatermarkError, match="SYNTHETIC"):
        unseal_synthetic_keys(data_root)
