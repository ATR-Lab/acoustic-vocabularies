"""Run-directory layout, public/restricted policy and masking findings."""

from pathlib import Path

import pytest

from av_generation.ids import RunKind
from av_generation.masking import drop_method_fields, masking_findings
from av_generation.rundir import (
    E_EXISTS,
    E_POLICY,
    E_RUN_ID,
    LOG_FILES,
    RunPolicyError,
    check_run_id,
    create_run_dir,
    relative_files,
    run_layout,
)

ROOT = Path(__file__).resolve().parents[2]


def test_layout_paths(tmp_path):
    layout = run_layout(tmp_path, "DEMO-run-01")
    assert layout.manifest == tmp_path / "DEMO-run-01" / "run-manifest.json"
    assert layout.log("slot") == layout.root / "logs" / "slots.jsonl"
    assert layout.audio("f" * 64).name == "f" * 64 + ".wav"
    assert layout.audit_masked_dir.parts[-2:] == ("audit", "masked")
    assert layout.bank_dir("bank-C001").parts[-2:] == ("banks", "bank-C001")
    assert layout.config.name == "config.json" and layout.store_dir.name == "store"
    assert layout.threshold_dir.name == "threshold"
    assert layout.audit_unmasked_dir.name == "unmasked"
    with pytest.raises(KeyError):
        layout.log("nope")
    assert len(set(LOG_FILES.values())) == len(LOG_FILES)


def test_run_ids():
    assert check_run_id("DEMO-dry-01", "synthetic") == "DEMO-dry-01"
    for run_id, kind in [("dry-01", "demo"), ("DEMO-x1", "pilot"), ("a|b", "pilot")]:
        with pytest.raises(RunPolicyError) as err:
            check_run_id(run_id, kind)
        assert err.value.code == E_RUN_ID


def test_restricted_runs_refused_inside_git_tree(tmp_path):
    with pytest.raises(RunPolicyError) as err:
        create_run_dir(ROOT / "generation" / "out", "PILOT-run-01", RunKind.PILOT)
    assert err.value.code == E_POLICY
    assert not (ROOT / "generation" / "out" / "PILOT-run-01").exists()
    fake_repo = tmp_path / "repo"
    (fake_repo / ".git").mkdir(parents=True)
    with pytest.raises(RunPolicyError):
        create_run_dir(fake_repo / "runs", "C-run-01", "confirmatory")


def test_demo_runs_and_restricted_runs_outside_git(tmp_path):
    demo = create_run_dir(tmp_path, "DEMO-run-01", "demo")
    assert demo.logs_dir.is_dir()
    (demo.root / "logs" / "slots.jsonl").write_bytes(b"")
    assert relative_files(demo.root) == ["logs/slots.jsonl"]
    with pytest.raises(RunPolicyError) as err:
        create_run_dir(tmp_path, "DEMO-run-01", "demo")
    assert err.value.code == E_EXISTS
    restricted = create_run_dir(tmp_path, "P-run-01", "pilot")
    assert restricted.root.is_dir()


def test_masking_findings():
    assert masking_findings("book BK-C-7QX4MN, atom K-a1, 12 slots, rater R1, station S1") == ()
    found = masking_findings("A3 book; designer D2; LLM tokens_in; seed_key=x; hand-designed")
    assert any("'A3'" in f for f in found)
    assert any("'D2'" in f for f in found)
    assert any("LLM" in f for f in found)
    assert any("seed_key" in f for f in found)
    assert any("hand-designed" in f for f in found)
    assert masking_findings("Qwen2.5 mutation") and masking_findings("vllm")
    assert masking_findings("Bayesian", extra_words=["bayes"])
    assert drop_method_fields({"method": "A1", "book_id": "x", "seed": 1}) == {"book_id": "x"}
