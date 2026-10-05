"""The cross-runner comparison `sound/tools/compare_golden_digests.py` (#12)."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "tests" / "golden" / "manifest.json"


def load_tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "sound" / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool("compare_golden_digests")
DIGESTS = json.loads(MANIFEST.read_text(encoding="utf-8"))["digests"]
LABELS = ["linux-arm64", "linux-x86_64", "macos-arm64", "windows-x86_64"]


def record(label: str, **changes: Any) -> dict[str, Any]:
    data = {
        "runner": label,
        "system": "Linux",
        "machine": "x86_64",
        "python": "3.11.15",
        "numpy": "2.4.6",
        "renderer_hash": "r" * 64,
        "counts": {"recipe": 183, "atom": 48, "message": 96, "nonlexical": 7, "store": 3},
        "digests": copy.deepcopy(DIGESTS),
        "matches_manifest": True,
        "mismatches": 0,
    }
    data.update(changes)
    return data


def write(directory: Path, records: list[dict[str, Any]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for r in records:
        (directory / f"{r['runner']}.json").write_text(json.dumps(r), encoding="utf-8")
    return directory


def test_identical_runners_pass_and_print_one_row_each(tmp_path, capsys):
    folder = write(tmp_path / "d", [record(x) for x in LABELS])
    code = tool.main([str(folder), "--expect", ",".join(LABELS), "--manifest", str(MANIFEST)])
    out = capsys.readouterr().out
    assert code == 0
    assert "All 4 runners produced identical golden digests" in out
    rows = [line for line in out.splitlines() if line.startswith("| ") and "`" in line]
    assert [row.split(" | ")[0][2:] for row in rows] == sorted(LABELS)
    assert f"`{DIGESTS['all'][:12]}`" in rows[0]


def test_one_differing_category_fails():
    bad = record("windows-x86_64")
    bad["digests"]["store"] = "f" * 64
    ok, text = tool.compare([record("linux-x86_64"), bad], [], DIGESTS)
    assert not ok and "`windows-x86_64`: store digest differs" in text
    assert "**`ffffffffffff`**" in text


def test_runners_are_compared_with_each_other_without_a_manifest():
    first, second = record("a"), record("b")
    second["digests"]["all"] = "0" * 64
    ok, text = tool.compare([first, second], ["a", "b"])
    assert not ok and "`b`: all digest differs" in text


def test_missing_duplicate_and_failed_runners_fail():
    ok, text = tool.compare([record("linux-x86_64")], LABELS, DIGESTS)
    assert not ok and "runner `macos-arm64` produced no digest record" in text
    ok, text = tool.compare([record("a"), record("a")], ["a"], DIGESTS)
    assert not ok and "more than one digest record" in text
    ok, text = tool.compare([record("a", matches_manifest=False, mismatches=3)], ["a"], DIGESTS)
    assert not ok and "3 mismatches" in text and "**NO**" in text
    ok, text = tool.compare([record("a"), record("b", renderer_hash="s" * 64)], [], DIGESTS)
    assert not ok and "renderer_hash differs" in text
    ok, text = tool.compare([], [], DIGESTS)
    assert not ok and "no digest records" in text
