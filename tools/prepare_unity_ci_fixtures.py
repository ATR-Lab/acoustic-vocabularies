"""Prepare every synthetic producer fixture required by the strict Unity test wrapper.

Run with the locked banks environment. Nothing here supplies a Unity license, a
Windows runner, recorded Isaac evidence, study assets or participant authority.
The environment file is written last; a partial preparation must not be reused.
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), *(str(ROOT / name / "src") for name in ("sound", "schedules"))]

from tools import menu_store_bridge as bridge
from tools.menu_store_fixture import prepare_fixture
from tools.prepare_engine_histories import _example_module
from tools.prepare_engine_histories import prepare as histories
from tools.prepare_integrity_fixtures import prepare as integrity
from tools.prepare_joined_engineering import digest, json_bytes, local_path, need, write_new
from tools.prepare_qualified_join_fixture import prepare as joined
from tools.qualified_bank_fixture import build as qualified


def readonly_snapshots(config, output):
    """Commit the actual DEMO store once; retain exact wave-boundary snapshots."""
    from av_sound.grammar import ATOM_IDS
    from av_sound.package import ATOM_WAVES

    output.mkdir()
    config = dict(
        config, unit_id="DEMO-B-C01", book_id="DEMO-CI-READONLY", store_root=str(output / "store")
    )
    bridge.write_new(output / "config.json", config)
    store = bridge.MenuStoreBridge(config)
    previous, retained = None, []
    atoms = [None, *sorted(ATOM_IDS, key=lambda atom: (ATOM_WAVES[atom], ATOM_IDS.index(atom)))]
    for index, atom in enumerate(atoms):
        request = dict(
            schema_version=1,
            request_id=f"{index:032x}",
            operation="atom" if atom else "profile",
            unit_id=config["unit_id"],
            book_id=config["book_id"],
            bank_sha256=config["bank_sha256"],
            package_sha256=config["package_sha256"],
            expected_head=previous["after_head"] if previous else None,
            expected_snapshot_sha256=previous["after_snapshot_sha256"]
            if previous
            else bridge.EMPTY_SNAPSHOT,
            profile="P1",
            menu_key=atom or "profile",
            rank=1 if atom else None,
        )
        previous = store.select(request)
        need(previous["accepted"] is True, "UNITY_CI_STORE_REFUSED")
        if atom and index in (8, 12, 16):
            snapshot = store.verified_snapshot(
                expected_head=previous["after_head"],
                expected_snapshot_sha256=previous["after_snapshot_sha256"],
            )
            need(len(snapshot["entries"]) == index, "UNITY_CI_STORE_GROWTH")
            bridge.write_new(output / f"snapshot-{index}.json", snapshot)
            retained.append(index)
    need(
        retained == [8, 12, 16] and len(store.store.records(store.book_id)) == 17,
        "UNITY_CI_STORE_INCOMPLETE",
    )
    bridge.write_new(
        output / "summary.json",
        dict(
            config_sha256=store.config_sha256,
            participant_ready=False,
            timing_qualified=False,
            audio_played=False,
            files={p.name: digest(p.read_bytes()) for p in sorted(output.glob("*.json"))},
        ),
    )


def prepare(output):
    from av_sound.dyad_bank import synthetic_dyad_bank
    from av_sound.nonlexical import build_reserved_registry, nonlexical_assets
    from av_sound.package import load_package
    from av_sound.store import canonical_json
    from av_sound.wav import write_wav

    output = local_path(Path(output).absolute())
    need(
        any(part in (".local", "private", "local-data") for part in output.parts[1:]),
        "UNITY_CI_PRIVATE_OUTPUT",
    )
    need(not output.exists(), "UNITY_CI_OUTPUT_EXISTS")
    output.mkdir(parents=True)
    example = _example_module()
    packages = output / "producer-packages"
    packages.mkdir()
    example.build_a_example(packages / "package-demo")
    example.build_b_example(packages / "dyad-demo")
    package = load_package(packages / "dyad-demo")
    need(package.demo, "UNITY_CI_NONDEMO_PACKAGE")
    grammar = output / "reserved-assets"
    grammar.mkdir()
    need(
        json.loads((ROOT / "sound/reserved/registry.json").read_bytes())
        == build_reserved_registry().to_dict(),
        "UNITY_CI_RESERVED_DRIFT",
    )
    for asset in nonlexical_assets():
        write_wav(asset, grammar / (asset.id + ".wav"))
    bank = synthetic_dyad_bank(example.BANK_ID, labels=example.permutation_labels(example.B_UNIT))
    bank_path = output / "provisional-bank.json"
    write_new(bank_path, canonical_json(bank.to_dict()))
    source = dict(
        schema_version=1,
        demo_only=True,
        unit_id="DEMO-UNIT",
        book_id="DEMO-CI-CODEC",
        bank_path=str(bank_path),
        bank_file_sha256=digest(bank_path.read_bytes()),
        bank_sha256=bank.bank_sha256(),
        package_path=str(packages / "dyad-demo"),
        package_sha256=package.package_sha256,
        store_root=str(output / "unused-source-store"),
    )
    source_path = output / "source-config.local.json"
    write_new(source_path, json_bytes(source))
    codec = output / "menu-codec"
    prepare_fixture(source_path, digest(source_path.read_bytes()), codec, mode="codec")
    readonly = output / "readonly-store"
    readonly_snapshots(source, readonly)
    qbank = output / "qualified-bank"
    qualified(qbank)
    qjoin = joined(qbank, output / "qualified-join")
    integrity_root = output / "integrity"
    integrity_pin = integrity(integrity_root)
    history_root = output / "engine-history"
    history_pin = histories(history_root)
    environment = dict(
        AV_PACKAGE_DEMO_ROOT=str(packages),
        AV_GRAMMAR_ASSETS=str(grammar),
        AV_MENU_STORE_INTEROP=str(codec),
        AV_JOINED_READONLY_STORE=str(readonly),
        AV_QUALIFIED_BANK_ROOT=str(qbank),
        AV_SIMULATION_JOINED_CONFIG=qjoin["joined_config"],
        AV_SIMULATION_CAPABILITY=qjoin["capability"],
        AV_SIMULATION_EXPECT_QUALIFIED_BANK="1",
        AV_SIMULATION_EXPECT_MENU_ROLE="active",
        AV_INTEGRITY_FIXTURES=str(integrity_root / "fixtures.local.json"),
        AV_INTEGRITY_FIXTURES_SHA256=integrity_pin,
        AV_INTEGRITY_RESULTS=str(output / "integrity-exports"),
        AV_ENGINE_HISTORY_FIXTURES=str(history_root / "fixtures.local.json"),
        AV_ENGINE_HISTORY_FIXTURES_SHA256=history_pin,
        AV_ENGINE_HISTORY_RESULTS=str(output / "engine-history-exports"),
    )
    write_new(output / "environment.local.json", json_bytes(environment))
    return environment


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="fresh private output directory")
    args = parser.parse_args()
    prepare(args.out)
    print("Prepared synthetic Unity test fixtures; external Windows/license gate remains separate.")
