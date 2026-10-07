"""Command line of the confirmatory campaign (#28): plan, run, status, rebuild, verify-all,
rehearse. `run` is driven with the DEMO proposer (the #16 client and #17 prompt set are
replaced by monkeypatching), and #17's ledger by the test ledger."""

import json
from types import SimpleNamespace

import pytest
from av_generation.jsonio import write_document

import av_banks.builder as builder_module
import av_banks.cli as banks_cli
from av_banks.confirmatory import cli
from av_banks.confirmatory import rehearsal as R
from av_banks.confirmatory.common import CampaignLayout
from av_banks.confirmatory.runner import campaign_status, run_campaign


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("cli-inputs")
    config = R.demo_config()
    config.write(root / "generation-config.json")
    write_document(root / "freeze.json", R.demo_freeze_manifest(config))
    return SimpleNamespace(root=root, config=config, units=R.demo_units(root / "units"))


def _plan_args(inputs, root, *extra):
    return [
        "plan",
        "--campaign-root",
        str(root),
        "--campaign-id",
        "DEMO-ccli-01",
        "--generation-config",
        str(inputs.root / "generation-config.json"),
        "--freeze-manifest",
        str(inputs.root / "freeze.json"),
        "--units",
        str(inputs.units),
        *(x for ns in R.DEMO_PILOT for x in ("--pilot-namespace", ns)),
        *extra,
    ]


@pytest.fixture
def patched_run(monkeypatch, kit):
    """`run` with the DEMO proposer (modes per bank) and the test ledger."""
    modes = {}

    def proposer(args, config):
        assert args.llm_url == "http://127.0.0.1:9" and config.demo
        return lambda layout, bank: R.DemoSlotProposer(mode=modes.get(bank.bank_id, "valid"))

    def run(root, **kw):
        return run_campaign(root, ledger_factory=kit.Ledger, fsync=False, **kw)

    monkeypatch.setattr(cli, "real_proposer", proposer)
    monkeypatch.setattr(cli, "run_campaign", run)
    return modes


def _run_args(root, *extra):
    return [
        "run",
        str(root),
        "--meanings",
        "m",
        "--prompts",
        "p",
        "--decoding-schema",
        "d.json",
        "--llm-url",
        "http://127.0.0.1:9",
        "--workers",
        "1",
        *extra,
    ]


def test_cli_plan_run_status_rebuild_verify(tmp_path, inputs, patched_run, capsys):
    root = tmp_path / "campaign"
    assert cli.main(_plan_args(inputs, root, "--parallel-banks", "2")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["banks"] == 72 and out["first"] == "DEMO-C001" and out["last"] == "DEMO-C072"
    assert out["distinct_seeds"] == out["seed_keys"] == 165_888
    assert out["generation_config_sha256"] == inputs.config.frozen_sha256()
    assert cli.main(_plan_args(inputs, root)) == 2
    assert "E_EXISTS" in capsys.readouterr().err

    patched_run["DEMO-C002"] = "outage"
    assert (
        cli.main(_run_args(root, "--only", "DEMO-C001", "--only", "DEMO-C002", "--breaker", "3"))
        == 1
    )
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["halted"] and [b["outcome"] for b in out["banks"]] == ["complete", "crashed"]
    assert "DEMO-ccli-01: 1/72 done" in captured.err  # the final progress line

    assert cli.main(["status", str(root)]) == 0
    assert "1 crashed" in capsys.readouterr().out
    assert cli.main(["status", str(root), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["counts"]["crashed"] == 1 and len(status["banks"]) == 72

    assert cli.main(["rebuild", str(root), "--bank-id", "DEMO-C003", "--reason", "x"]) == 2
    assert "only a crashed bank" in capsys.readouterr().err
    assert cli.main(["rebuild", str(root), "--bank-id", "DEMO-C002", "--reason", "outage"]) == 0
    assert json.loads(capsys.readouterr().out)["bank_version"] == "1.0.1"
    patched_run.clear()
    assert cli.main(_run_args(root, "--only", "DEMO-C002", "--continue-on-error")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["banks"][0]["outcome"] == "complete" and out["halted"] is None

    assert cli.main(["verify-all", str(root), "--jobs", "2"]) == 0
    text = capsys.readouterr().out
    assert "DEMO-C001 v1.0.0 complete OK" in text and "DEMO-C002 v1.0.1 complete OK" in text
    assert cli.main(["register", str(root)]) == 2
    assert "70 banks are not finished" in capsys.readouterr().err
    assert campaign_status(root).counts["complete"] == 2


def test_cli_rehearse(tmp_path, kit, monkeypatch, capsys):
    monkeypatch.setattr(builder_module, "slot_ledger", kit.Ledger)
    code = cli.main(
        ["rehearse", "--out", str(tmp_path / "r"), "--only", "DEMO-C001", "--parallel-banks", "1"]
    )
    assert code == 1  # 71 banks are still pending, so no register
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["complete"] == 1 and out["decision"] is None
    layout = CampaignLayout.at(tmp_path / "r" / "campaign")
    assert layout.plan.is_file() and layout.verification_log.is_file()


def test_real_proposer_wraps_the_bank_builder_proposer(monkeypatch, inputs):
    seen = []

    def make_proposer(args, config):
        seen.append((args.llm_url, config))
        return lambda layout: ("proposer", layout)

    monkeypatch.setattr(banks_cli, "make_proposer", make_proposer)
    args = SimpleNamespace(llm_url="http://127.0.0.1:9")
    factory = cli.real_proposer(args, inputs.config)
    assert factory("layout", "bank") == ("proposer", "layout")
    assert seen == [("http://127.0.0.1:9", inputs.config)]


def test_python_dash_m_entry_point(tmp_path):
    import runpy
    import sys

    argv = sys.argv
    sys.argv = ["av_banks.confirmatory", "status", str(tmp_path / "missing")]
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("av_banks.confirmatory", run_name="__main__")
    finally:
        sys.argv = argv
    assert exc.value.code == 2
