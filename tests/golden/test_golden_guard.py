"""The golden version guard `sound/tools/check_golden_bump.py` (#12), on synthetic manifests."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]


def load_tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "sound" / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = load_tool("check_golden_bump")

RULES = {
    "recipe": ["renderer_version"],
    "atom": ["renderer_version"],
    "message": ["renderer_version"],
    "nonlexical": ["renderer_version", "asset_spec_version"],
    "store": ["renderer_version", "validator_version", "store_record_version"],
}


def item(item_id: str, digest: str = "a" * 64) -> dict[str, Any]:
    category = item_id.split("/")[0]
    return {
        "id": item_id,
        "category": category,
        "inputs": {"name": item_id},
        "outputs": {"pcm_sha256": digest},
    }


def manifest(*items: dict[str, Any], **header: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": "av-sound golden manifest",
        "renderer_version": "0.1.0",
        "renderer_hash": "r" * 64,
        "validator_version": "0.1.0",
        "asset_spec_version": "0.1.0",
        "store_record_version": 1,
        "version_rules": RULES,
        "items": list(items),
    }
    data.update(header)
    return data


BASE = manifest(
    item("recipe/a/P1"),
    item("nonlexical/ready-cue"),
    item("store/DEMO-P1"),
)


def changed(base: dict[str, Any], item_id: str, **header: Any) -> dict[str, Any]:
    head = copy.deepcopy(base)
    head.update(header)
    for entry in head["items"]:
        if entry["id"] == item_id:
            entry["outputs"]["pcm_sha256"] = "b" * 64
    return head


def test_unchanged_and_added_items_pass():
    assert guard.check(BASE, copy.deepcopy(BASE)).ok
    head = copy.deepcopy(BASE)
    head["items"].append(item("recipe/new/P1"))
    report = guard.check(BASE, head)
    assert report.ok and report.added == ["recipe/new/P1"] and not report.changed


def test_no_base_manifest_passes():
    report = guard.check(None, BASE)
    assert report.ok and len(report.added) == 3
    assert guard.check(None, None).ok


def test_changed_hash_without_bump_fails():
    report = guard.check(BASE, changed(BASE, "recipe/a/P1"))
    assert not report.ok
    assert report.changed == [("recipe/a/P1", "outputs.pcm_sha256")]
    assert "renderer_version" in report.violations[0]
    text = guard.markdown(report, "origin/main")
    assert "Result: **fail**" in text and "`recipe/a/P1`" in text


def test_changed_hash_with_renderer_bump_passes_and_asks_for_a_reviewer_note():
    report = guard.check(BASE, changed(BASE, "recipe/a/P1", renderer_version="0.2.0"))
    assert report.ok and report.bumps == {"renderer_version": ("0.1.0", "0.2.0")}
    text = guard.markdown(report, "origin/main")
    assert "Result: pass" in text and "reviewer note" in text


@pytest.mark.parametrize(
    ("item_id", "header", "ok"),
    [
        ("store/DEMO-P1", {"validator_version": "0.2.0"}, True),
        ("store/DEMO-P1", {"store_record_version": 2}, True),
        ("store/DEMO-P1", {"asset_spec_version": "0.2.0"}, False),
        ("nonlexical/ready-cue", {"asset_spec_version": "0.2.0"}, True),
        ("nonlexical/ready-cue", {"validator_version": "0.2.0"}, False),
        ("recipe/a/P1", {"validator_version": "0.2.0"}, False),
        ("recipe/a/P1", {"asset_spec_version": "1.0.0"}, False),
        ("recipe/a/P1", {"renderer_version": "1.0.0"}, True),
    ],
)
def test_each_category_accepts_only_its_own_versions(item_id, header, ok):
    assert guard.check(BASE, changed(BASE, item_id, **header)).ok is ok


def test_removed_item_needs_a_bump():
    head = copy.deepcopy(BASE)
    head["items"] = head["items"][1:]
    report = guard.check(BASE, head)
    assert not report.ok and report.removed == ["recipe/a/P1"]
    head["renderer_version"] = "0.1.1"
    assert guard.check(BASE, head).ok


def test_changed_inputs_count_as_a_change():
    head = copy.deepcopy(BASE)
    head["items"][0]["inputs"]["name"] = "other"
    report = guard.check(BASE, head)
    assert not report.ok and report.changed == [("recipe/a/P1", "inputs")]


def test_versions_must_increase():
    for value in ("0.0.9", "0.1.0-rc", None):
        report = guard.check(BASE, changed(BASE, "recipe/a/P1", renderer_version=value))
        assert not report.ok
        assert any("must increase" in v for v in report.violations)
    assert not guard.check(BASE, manifest(*BASE["items"], store_record_version=0)).ok
    assert guard.version_key(True) is None and guard.version_key(3) == (3,)


def test_rules_come_from_the_base_manifest():
    # A pull request cannot relax the rules: the head's rules are ignored.
    head = changed(BASE, "recipe/a/P1", validator_version="0.2.0")
    head["version_rules"] = {**RULES, "recipe": ["validator_version"]}
    assert not guard.check(BASE, head).ok
    # Without rules in the base, every category needs a renderer bump.
    bare = {k: v for k, v in BASE.items() if k != "version_rules"}
    assert not guard.check(bare, changed(bare, "store/DEMO-P1", validator_version="0.2.0")).ok
    assert guard.check(bare, changed(bare, "store/DEMO-P1", renderer_version="0.2.0")).ok


def test_deleted_manifest_fails():
    report = guard.check(BASE, None)
    assert not report.ok and "deleted" in report.violations[0]


def test_renderer_hash_change_alone_passes_with_a_note():
    report = guard.check(BASE, manifest(*BASE["items"], renderer_hash="s" * 64))
    assert report.ok and any("renderer_hash" in n for n in report.notes)


# --- Against a real git history ---------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), "-c", "user.name=golden-test"]
    cmd += ["-c", "user.email=golden-test@example.invalid", "-c", "commit.gpgsign=false"]
    return subprocess.run([*cmd, *args], check=True, capture_output=True, text=True).stdout


def write_manifest(repo: Path, data: dict[str, Any]) -> None:
    path = repo / guard.MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_guard_against_the_merge_base(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("synthetic\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start without goldens")
    git(repo, "branch", "-M", "base")
    git(repo, "checkout", "-q", "-b", "first-goldens")
    write_manifest(repo, BASE)
    assert guard.main(["base", "--repo", str(repo)]) == 0  # no manifest at the merge base
    assert "every item is new" in capsys.readouterr().out

    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add goldens")
    git(repo, "checkout", "-q", "base")
    git(repo, "merge", "-q", "--ff-only", "first-goldens")
    git(repo, "checkout", "-q", "-b", "change")
    write_manifest(repo, changed(BASE, "recipe/a/P1"))
    assert guard.main(["base", "--repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "Result: **fail**" in out and "`recipe/a/P1`" in out

    write_manifest(repo, changed(BASE, "recipe/a/P1", renderer_version="0.2.0"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "renderer change with bump")
    assert guard.main(["base", "--repo", str(repo)]) == 0
    assert "`renderer_version` 0.1.0 -> 0.2.0" in capsys.readouterr().out

    (repo / guard.MANIFEST).unlink()
    assert guard.main(["base", "--repo", str(repo)]) == 1
    assert guard.main(["no-such-ref", "--repo", str(repo)]) == 2
