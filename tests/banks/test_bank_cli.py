"""Bank runs (run directory, run manifest, parallel banks, policy) and the command line."""

import json
import shutil
from pathlib import Path

import pytest
from av_generation.clock import ManualClock
from av_generation.jsonio import file_sha256, read_json
from av_generation.records import RunManifest, TimingEvent, read_records
from av_generation.rundir import RunPolicyError

import av_banks.cli as cli
from av_banks.builder import BankBuildError, bank_spec
from av_banks.layout import BankLayout
from av_banks.permutation import load_permutation
from av_banks.run import build_banks, clock_kind
from av_banks.verify import verify_bank


def _run(kit, tmp_path, specs, **kwargs):
    script = kit.script(kit.all_kind("valid"))
    return build_banks(
        specs,
        runs_root=tmp_path,
        run_id=kwargs.pop("run_id", "DEMO-bank-run-01"),
        config=kwargs.pop("config", kit.config),
        proposer=kwargs.pop("proposer", kit.proposer(script)),
        clock=ManualClock(),
        ledger_factory=kit.Ledger,
        fsync=False,
        **kwargs,
    )


def test_a_run_builds_several_banks_in_parallel(kit, tmp_path):
    specs = [kit.spec("DEMO-bank-01"), kit.spec("DEMO-bank-02")]
    result = _run(kit, tmp_path, specs, parallel_banks=2, workers=3)
    assert [b.status for b in result.banks] == ["complete", "complete"]
    assert result.banks[0].bank_sha256 != result.banks[1].bank_sha256  # own seeds
    manifest = RunManifest.read(result.run_dir / "run-manifest.json")
    assert manifest.purpose == "bank" and manifest.study.value == "B"
    assert manifest.bank_ids == ("DEMO-bank-01", "DEMO-bank-02")
    assert manifest.generation_config_sha256 == kit.config.frozen_sha256()
    assert manifest.meanings_sha256 == kit.meanings.sha256()
    assert manifest.seed_namespace is None and manifest.clock == "manual"
    assert file_sha256(result.run_dir / "run-manifest.json") == result.run_manifest_sha256
    for name, digest in manifest.files.items():
        assert file_sha256(result.run_dir / name) == digest
    assert "banks/DEMO-bank-02/manifest.json" in manifest.files
    events = [e.event for e in read_records(result.run_dir / "logs/timing.jsonl", TimingEvent)]
    assert events == ["run_start", "run_end"]
    for bank in result.banks:
        assert verify_bank(bank.bank_dir).ok
        assert bank.bank_dir == result.run_dir / "banks" / bank.bank_id


def test_proposer_factory_gets_the_run_layout(kit, tmp_path):
    seen = []

    def factory(layout):
        seen.append(layout)
        return kit.proposer(kit.script(kit.all_kind("valid")))

    result = _run(kit, tmp_path, [kit.spec()], proposer=factory, workers=1)
    assert seen[0].root == result.run_dir and seen[0].run_id == "DEMO-bank-run-01"
    assert RunManifest.read(result.run_dir / "run-manifest.json").seed_namespace == "DEMO-bank-01"


def _pilot(kit, tmp_path, bank_id="bank-P001", unit_id="B-P01"):
    doc = read_json(kit.DEMO_UNIT)
    doc.update(demo=False, set="pilot", unit_id=unit_id, seed_label="sha256:" + "6" * 64)
    path = tmp_path / unit_id / "permutation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return bank_spec(bank_id, load_permutation(path))


def test_restricted_runs_are_refused_inside_a_git_work_tree(kit, tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    real = kit.make_config("pilot-1", prompt_set=kit.prompt_set, llm="b" * 64)
    spec = _pilot(kit, tmp_path)
    with pytest.raises(RunPolicyError, match="git work tree"):
        _run(kit, tmp_path / "repo", [spec], config=real, run_id="P-banks-01")
    assert not (tmp_path / "repo" / "P-banks-01").exists()
    builder = kit.builder(
        tmp_path / "repo", kit.script(kit.all_kind("valid")), spec=spec, config=real
    )
    with pytest.raises(RunPolicyError, match="git work tree"):
        builder.build()
    assert not (tmp_path / "repo" / "bank-P001").exists()


def test_run_rules(kit, tmp_path):
    with pytest.raises(BankBuildError, match="nothing to build"):
        _run(kit, tmp_path, [])
    with pytest.raises(BankBuildError, match="repeat"):
        _run(kit, tmp_path, [kit.spec(), kit.spec()])
    with pytest.raises(BankBuildError, match="one set"):
        _run(kit, tmp_path, [kit.spec(), _pilot(kit, tmp_path)])
    with pytest.raises(BankBuildError, match="parallel_banks"):
        _run(kit, tmp_path, [kit.spec()], parallel_banks=0)
    with pytest.raises(BankBuildError, match="cannot be built in a pilot run"):
        _run(kit, tmp_path, [kit.spec()], kind="pilot")
    assert list(tmp_path.iterdir()) == [tmp_path / "B-P01"]
    assert clock_kind(ManualClock()) == "manual"


# -- command line ------------------------------------------------------------


@pytest.fixture
def cli_bank(built_bank, tmp_path):
    target = tmp_path / built_bank.bank_id
    shutil.copytree(built_bank.bank_dir, target)
    return target


def test_cli_verify_and_hash(cli_bank, built_bank, capsys):
    assert cli.main(["verify", str(cli_bank)]) == 0
    out = capsys.readouterr().out
    assert "OK" in out and built_bank.bank_sha256 in out
    assert cli.main(["verify", str(cli_bank), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["pairs_checked"] == {"P1": 1920, "P2": 1920, "P3": 1920}
    assert cli.main(["hash", str(cli_bank)]) == 0
    assert capsys.readouterr().out.strip() == built_bank.bank_sha256
    BankLayout(cli_bank).bank_hash.write_text("0" * 64 + "\n", encoding="utf-8")
    assert cli.main(["verify", str(cli_bank)]) == 1
    assert "PROBLEM" in capsys.readouterr().out


def test_cli_amend(cli_bank, capsys):
    base = ["amend", str(cli_bank), "--profile", "P2", "--atom", "K-r3", "--rank", "1"]
    assert cli.main([*base, "--reason", "DEMO: unusable cached asset"]) == 2
    assert "E_HEARD" in capsys.readouterr().err
    code = cli.main(
        [
            *base,
            "--reason",
            "DEMO: unusable cached asset",
            "--unheard-confirmed",
            "--date",
            "2026-12-01",
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["amendment"]["replaced_option_id"] == "DEMO-bank-01.P2.K-r3.1"
    assert out["menu"][0] == "DEMO-bank-01.P2.K-r3.4"
    assert cli.main(["verify", str(cli_bank)]) == 0


def test_cli_build(kit, tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "generation-config.json"
    kit.config.write(config_path)
    schema_path = tmp_path / "decoding-schema.json"
    schema_path.write_text(json.dumps(kit.decoding_schema), encoding="utf-8")
    script = kit.script(kit.all_kind("valid"))
    made = []

    def make_proposer(args, config):
        made.append((args, config))
        return kit.proposer(script)

    real_build = cli.build_banks

    def build_with_memory_ledger(specs, **kwargs):
        return real_build(specs, ledger_factory=kit.Ledger, fsync=False, **kwargs)

    monkeypatch.setattr(cli, "make_proposer", make_proposer)
    monkeypatch.setattr(cli, "build_banks", build_with_memory_ledger)
    args = [
        "build",
        "--bank-id", "DEMO-bank-07",
        "--permutation", str(kit.DEMO_UNIT),
        "--runs-root", str(tmp_path / "runs"),
        "--run-id", "DEMO-cli-run",
        "--generation-config", str(config_path),
        "--meanings", str(kit.root / "generation/examples/demo-meanings"),
        "--prompts", str(tmp_path / "prompts"),
        "--decoding-schema", str(schema_path),
        "--llm-url", "http://127.0.0.1:9",
        "--workers", "2",
    ]  # fmt: skip
    assert cli.main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    bank = summary["banks"][0]
    assert bank["status"] == "complete" and bank["attempts"] == 1
    assert made[0][1].frozen_sha256() == kit.config.frozen_sha256()
    assert verify_bank(Path(bank["bank_dir"])).bank_sha256 == bank["bank_sha256"]
    # an unavailable bank exits 3
    monkeypatch.setattr(
        cli, "make_proposer", lambda a, c: kit.proposer(kit.script(kit.all_kind("short")))
    )
    args[args.index("DEMO-cli-run")] = "DEMO-cli-run-2"
    assert cli.main(args) == 3
    capsys.readouterr()
    # argument errors exit 2
    assert cli.main([*args[:5], "--bank-id", "DEMO-bank-08", *args[5:]]) == 2
    assert "one --permutation per --bank-id" in capsys.readouterr().err
    two = [*args[:5], "--bank-id", "DEMO-bank-08", "--permutation", str(kit.DEMO_UNIT), *args[5:]]
    assert cli.main([*two, "--seed-namespace", "DEMO-ns"]) == 2
    assert "build one bank" in capsys.readouterr().err
    assert cli.main(args) == 2  # the run directory exists
    assert "already exists" in capsys.readouterr().err


def test_cli_build_checks_the_prompt_inputs_before_the_run(kit, tmp_path, monkeypatch, capsys):
    """The real `make_proposer` loads the meanings, prompt set (#17) and decoding schema
    and compares their hashes with the config before any run directory exists."""
    config_path = tmp_path / "generation-config.json"
    kit.config.write(config_path)
    schema_path = tmp_path / "decoding-schema.json"
    schema_path.write_text(json.dumps(dict(kit.decoding_schema, title="other")), "utf-8")
    monkeypatch.setattr(cli, "load_prompt_set", lambda path, meanings: kit.prompt_set)
    args = [
        "build",
        "--bank-id", "DEMO-bank-09",
        "--permutation", str(kit.DEMO_UNIT),
        "--runs-root", str(tmp_path / "runs"),
        "--run-id", "DEMO-cli-run-9",
        "--generation-config", str(config_path),
        "--meanings", str(kit.root / "generation/examples/demo-meanings"),
        "--prompts", str(tmp_path / "prompts"),
        "--decoding-schema", str(schema_path),
        "--llm-url", "http://127.0.0.1:9",
    ]  # fmt: skip
    assert cli.main(args) == 2
    assert "decoding schema" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()
    schema_path.write_text(json.dumps(kit.decoding_schema), "utf-8")
    factory = cli.make_proposer(cli._parser().parse_args(args), kit.config)
    assert callable(factory)
