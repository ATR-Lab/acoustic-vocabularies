"""Reject a valid-looking frame registry bound to the wrong robot/layout."""
import copy
import csv
import json
from pathlib import Path

import pytest

from isaac.publisher.benchmark import registry_from_snapshot
from isaac.publisher.protocol import PublicRegistry

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    with (ROOT/"docs/spikes/isaac/joint_inventory.csv").open(newline="") as stream:
        names = [row["name"] for row in csv.DictReader(stream)]
    layout = json.loads((ROOT/"apparatus/workcell_layout.json").read_text())
    snapshot = dict(scene_sha256="a"*64, state=dict(robot=dict(joint_names=names),
                    objects={obj["id"]: copy.deepcopy(obj) for obj in layout["objects"]}))
    return layout, snapshot


def bind(layout, snapshot):
    return registry_from_snapshot(layout, snapshot, "b"*64, "station-01",
                                  ROOT/"docs/spikes/isaac/joint_inventory.csv")


def test_registry_binds_measured_map_to_complete_scene():
    layout, snapshot = inputs()
    registry = bind(layout, snapshot)
    assert len(registry.joint_names) == 43
    assert len(registry.object_states) == len(layout["objects"])
    assert registry.scene_sha256 == snapshot["scene_sha256"]


def test_same_joints_in_different_order_are_not_silently_reinterpreted():
    layout, snapshot = inputs()
    snapshot["state"]["robot"]["joint_names"].reverse()
    with pytest.raises(ValueError, match="joint order"):
        bind(layout, snapshot)


def test_missing_neutral_object_blocks_registry():
    layout, snapshot = inputs()
    snapshot["state"]["objects"].pop(next(iter(snapshot["state"]["objects"])))
    with pytest.raises(ValueError, match="inventory"):
        bind(layout, snapshot)


def test_station_ids_match_foundation_contract():
    layout, snapshot = inputs()
    registry = bind(layout, snapshot)
    for station in ("A", "lab_station.02", "a"*80):
        PublicRegistry(station, registry.scene_sha256, registry.reset_snapshot_sha256,
                       registry.joint_names, registry.object_states, registry.anchor_ids)
    for station in ("", "_station", "station/01", "a"*81):
        with pytest.raises(ValueError):
            PublicRegistry(station, registry.scene_sha256, registry.reset_snapshot_sha256,
                           registry.joint_names, registry.object_states, registry.anchor_ids)
