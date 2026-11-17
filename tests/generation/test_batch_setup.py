"""Batch configs, rebuilt configs and the generation config from one command each (#311).

The schedules files come from the schedules package itself (`av_schedules`, standard
library only), run in process on a DEMO seed and on a test master seed in a temporary
folder outside any git work tree."""

import csv
import hashlib
import json
import secrets
import sys
from pathlib import Path

import pytest

from av_generation import batch_setup as bs
from av_generation.batch_runner import check_batch_start, load_batch_inputs
from av_generation.config import BatchConfig
from av_generation.constants import PANEL_ORDERS
from av_generation.genconfig import GenerationConfig

REPO = Path(__file__).resolve().parents[2]
DEMO_FALLBACK = REPO / "sound" / "testvectors" / "fallback" / "demo-manifest.json"
DEMO_MEANINGS = REPO / "generation" / "examples" / "demo-meanings"
SEATS = {
    "A-P01": "R01@S1,R02@S2,R03@S3",
    "A-P02": "R04@S1,R05@S2,R06@S3",
    "A-P03": "R07@S1,R08@S2,R09@S3",
}


def _schedules_main(argv):
    sys.path.insert(0, str(REPO / "schedules" / "src"))
    try:
        from av_schedules.cli import main
    finally:
        sys.path.remove(str(REPO / "schedules" / "src"))
    assert main(argv) == 0, argv


def _schedules(out: Path, seed_args: list[str], alloc_args: list[str]) -> Path:
    _schedules_main(["curriculum", *seed_args, "--study", "A", "--set", "pilot", "--out", str(out)])
    _schedules_main(["allocate", *alloc_args, "--study", "A", "--out", str(out)])
    return out / "A"


@pytest.fixture(scope="module")
def demo_units(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("demo-schedules")
    return _schedules(
        out, ["--demo-seed", "DEMO-batch-setup"], ["--pilot-demo-seed", "DEMO-batch-setup"]
    )


@pytest.fixture(scope="module")
def real_units(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("restricted")
    seed = out / "seed.txt"
    seed.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
    return _schedules(
        out / "schedules",
        ["--master-seed-file", str(seed)],
        ["--pilot-seed-file", str(seed)],
    )


def _seats(kind="human"):
    return {unit: bs.parse_seats(text, kind=kind) for unit, text in SEATS.items()}


def _build(units: Path, set_ns: str, **kwargs):
    args = {
        "batch_table": units / "pilot-batch-table.csv",
        "units": units,
        "book_key": units / "pilot-book-key.json",
        "fallback": DEMO_FALLBACK,
        "set_ns": set_ns,
        "seats": _seats(),
    }
    args.update(kwargs)
    return bs.build_set_configs(**args)


def _cli(argv, capsys):
    code = bs.main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else None), captured.err


def _configs_argv(units: Path, out: Path, ns: list[str], seats=SEATS):
    argv = [
        "configs",
        "--batch-table",
        units / "pilot-batch-table.csv",
        "--units",
        units,
        "--book-key",
        units / "pilot-book-key.json",
        "--fallback",
        DEMO_FALLBACK,
        *ns,
        "--out",
        out,
    ]
    for unit, text in seats.items():
        argv += ["--seats", f"{unit}={text}"]
    return argv


def test_demo_set_gives_three_runnable_configs(demo_units, tmp_path, capsys):
    out = tmp_path / "inputs"
    code, report, err = _cli(_configs_argv(demo_units, out, ["--demo-set-ns", "DEMO-ns"]), capsys)
    assert code == 0, err
    code, gen_report, err = _cli(
        [
            "generation-config",
            "--name",
            "DEMO-setup-1",
            "--meanings",
            DEMO_MEANINGS,
            "--fallback",
            DEMO_FALLBACK,
            "--out",
            out / "generation-config.json",
        ],
        capsys,
    )
    assert code == 0, err
    assert gen_report["demo"] is True
    assert gen_report["separation_threshold"] == bs.threshold_text()
    gen = GenerationConfig.read(out / "generation-config.json")
    assert gen.frozen_sha256() == gen_report["frozen_sha256"]

    table = list(csv.DictReader((demo_units / "pilot-batch-table.csv").open(encoding="utf-8")))
    orders = (out / bs.PANEL_ORDERS_NAME).read_bytes()
    assert report["panel_orders"]["sha256"] == hashlib.sha256(orders).hexdigest()
    assert [b["batch_id"] for b in report["batches"]] == [f"DEMO-{r['unit_id']}" for r in table]
    # the three pilot panels (one per profile) get three different orders
    assert len({b["order_index"] for b in report["batches"]}) == 3
    for row, entry in zip(table, report["batches"], strict=True):
        config = BatchConfig.read(entry["config"])
        assert config.set == "demo"
        assert config.sha256() == entry["config_sha256"]
        assert config.profile.value == row["profile"] == entry["profile"]
        assert config.atom_order == tuple(row["atom_order"].split("|"))
        assert config.panel.panel_id == f"{config.batch_id}-N1"
        assert [m.value for m in (config.method_of(b) for b in config.panel.order)] == list(
            PANEL_ORDERS[config.panel.order_index - 1]
        )
        a1 = next(b for b in config.books if b.designer_id)
        assert a1.designer_id == row["designer"] == entry["designer"]
        assert all(b.book_id.startswith("DEMO-BK-") for b in config.books)
        assert all(s.kind == "human" for s in config.panel.raters)
        sources = config.sources
        assert sources.panel_schedule_sha256 == report["panel_orders"]["sha256"]
        assert (
            sources.batch_table_sha256
            == hashlib.sha256((demo_units / "pilot-batch-table.csv").read_bytes()).hexdigest()
        )
        unit = row["unit_id"]
        assert (
            sources.permutation_sha256
            == hashlib.sha256((demo_units / unit / "permutation.json").read_bytes()).hexdigest()
        )
        inputs = load_batch_inputs(
            config=entry["config"],
            meanings=DEMO_MEANINGS,
            fallback=DEMO_FALLBACK,
            generation_config=out / "generation-config.json",
        )
        check_batch_start(inputs, kind="demo", run_id=f"DEMO-run-{unit}")

    # nothing is overwritten, and a refused write leaves the folder as it was
    before = sorted(p.relative_to(out) for p in out.rglob("*"))
    code, _, err = _cli(_configs_argv(demo_units, out, ["--demo-set-ns", "DEMO-ns"]), capsys)
    assert code == 2 and bs.E_EXISTS in err
    assert sorted(p.relative_to(out) for p in out.rglob("*")) == before


def test_real_set_uses_a_restricted_namespace_and_real_files(real_units, demo_units, tmp_path):
    ns_file = tmp_path / "set-ns.txt"
    ns_file.write_text("A-P-" + secrets.token_hex(8) + "\n", encoding="utf-8")
    set_ns = bs.read_set_namespace(ns_file)
    built = _build(real_units, set_ns)
    assert [c.set for c in built.configs] == ["pilot"] * 3
    assert [c.batch_id for c in built.configs] == ["A-P01", "A-P02", "A-P03"]
    assert not any(b.book_id.startswith("DEMO-") for c in built.configs for b in c.books)
    paths = bs.write_set_configs(built, tmp_path / "pilot-inputs")
    assert BatchConfig.read(paths["A-P02"]) == built.configs[1]
    # a real set inside a git work tree is refused before anything is created
    inside = REPO / "generation" / "out" / f"never-{secrets.token_hex(4)}"
    with pytest.raises(bs.BatchSetupError) as err:
        bs.write_set_configs(built, inside)
    assert err.value.code == bs.E_POLICY and not inside.exists()
    # DEMO files with a real namespace, and real files with a DEMO namespace
    for units, ns in ((demo_units, set_ns), (real_units, "DEMO-ns")):
        with pytest.raises(bs.BatchSetupError) as err:
            _build(units, ns)
        assert err.value.code == bs.E_INPUTS
    with pytest.raises(bs.BatchSetupError) as err:
        _build(real_units, set_ns, seats=_seats("bot"))
    assert err.value.code == bs.E_SEATS


@pytest.mark.parametrize(
    "seats",
    [
        {**SEATS, "A-P03": "R07@S1,R08@S2,R01@S3"},  # a rater on two panels
        {k: v for k, v in SEATS.items() if k != "A-P02"},  # a batch without seats
        {**SEATS, "A-P01": "R01@S1,R02@S2,R03@station3"},  # not <rater>@S<n>
    ],
)
def test_seats_are_checked(demo_units, tmp_path, capsys, seats):
    argv = _configs_argv(demo_units, tmp_path / "out", ["--demo-set-ns", "DEMO-ns"], seats)
    code, _, err = _cli(argv, capsys)
    assert code == 2 and bs.E_SEATS in err
    assert not (tmp_path / "out").exists()


def test_threshold_must_match_the_fallback_set(demo_units):
    with pytest.raises(bs.BatchSetupError) as err:
        _build(demo_units, "DEMO-ns", threshold="0.125")
    assert err.value.code == bs.E_INPUTS


@pytest.mark.parametrize("text", ["DEMO-" + "a" * 20, "A-P-short", "has space in it 123", ""])
def test_set_namespace_file_rules(tmp_path, text):
    path = tmp_path / "ns.txt"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(bs.BatchSetupError) as err:
        bs.read_set_namespace(path)
    assert err.value.code == bs.E_INPUTS


def test_set_namespace_file_inside_a_work_tree_is_refused():
    with pytest.raises(bs.BatchSetupError) as err:
        bs.read_set_namespace(REPO / "generation" / "README.md")
    assert err.value.code == bs.E_POLICY


def test_rebuild_gives_a_new_panel_aliases_and_seeds(demo_units, tmp_path, capsys):
    out = tmp_path / "inputs"
    code, report, err = _cli(_configs_argv(demo_units, out, ["--demo-set-ns", "DEMO-ns"]), capsys)
    assert code == 0, err
    old_path = report["batches"][0]["config"]
    old = BatchConfig.read(old_path)
    new_path = out / "DEMO-A-P01-rb1" / "config.json"
    argv = ["rebuild", "--config", old_path, "--demo-set-ns", "DEMO-ns"]
    code, rebuilt, err = _cli([*argv, "--seats", "R10@S1,R11@S2,R12@S3", "--out", new_path], capsys)
    assert code == 0, err
    new = BatchConfig.read(new_path)
    assert (rebuilt["panel_id"], rebuilt["seed_namespace"]) == ("DEMO-A-P01-N2", "DEMO-A-P01-rb1")
    assert new.panel.panel_id == "DEMO-A-P01-N2" and new.seed_namespace == "DEMO-A-P01-rb1"
    assert (new.books, new.atom_order, new.labels) == (old.books, old.atom_order, old.labels)
    assert new.panel.order == old.panel.order
    assert new.panel.aliases != old.panel.aliases
    assert bs.next_rebuild_ids(new) == ("DEMO-A-P01-N3", "DEMO-A-P01-rb2")
    # an old rater cannot sit on the rebuilt panel; a real config needs --set-ns-file
    again = tmp_path / "again.json"
    code, _, err = _cli([*argv, "--seats", "R01@S1,R11@S2,R12@S3", "--out", again], capsys)
    assert code == 2 and "independent panel" in err and not again.exists()
    code, _, err = _cli(
        ["rebuild", "--config", old_path, "--set-ns-file", tmp_path / "x", "--seats", "R1@S1"]
        + ["--out", again],
        capsys,
    )
    assert code == 2 and not again.exists()


def test_real_generation_config_stays_out_of_git(capsys):
    inside = REPO / "generation" / "out" / f"never-{secrets.token_hex(4)}" / "gen.json"
    code, _, err = _cli(
        ["generation-config", "--name", "pilot-1", "--meanings", DEMO_MEANINGS]
        + ["--fallback", DEMO_FALLBACK, "--out", inside],
        capsys,
    )
    assert code == 2 and bs.E_POLICY in err and not inside.parent.exists()


def test_generation_config_pins_the_inputs(tmp_path, capsys):
    out = tmp_path / "gen.json"
    code, report, err = _cli(
        ["generation-config", "--name", "pilot-1", "--meanings", DEMO_MEANINGS]
        + ["--fallback", DEMO_FALLBACK, "--out", out],
        capsys,
    )
    assert code == 0, err
    config = GenerationConfig.read(out)
    assert config.demo is False and config.name == "pilot-1"
    assert config.llm_manifest_sha256 is not None
    assert config.separation_threshold == bs.threshold_text()
    assert report["file_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    code, _, err = _cli(
        ["generation-config", "--name", "pilot-1", "--meanings", DEMO_MEANINGS]
        + ["--fallback", DEMO_FALLBACK, "--out", out],
        capsys,
    )
    assert code == 2 and bs.E_EXISTS in err


def test_threshold_text_needs_a_decimal_string(tmp_path):
    path = tmp_path / "validator.json"
    path.write_text(json.dumps({"separation_threshold": 0.1}), encoding="utf-8")
    with pytest.raises(bs.BatchSetupError):
        bs.threshold_text(path)
