"""O4.1 integration demo (#2): three synthetic books, recipes to sealed packages."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from av_sound import MAX_MESSAGE_SAMPLES, MIN_MESSAGE_SAMPLES, Profile, load_package

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "sound" / "tools" / "integration_demo.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("integration_demo", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load_tool()


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("integration-demo") / "out"
    assert tool.main(["--out", str(out)]) == 0
    return out, json.loads((out / "summary.json").read_text(encoding="utf-8"))


def test_three_books_are_committed_verified_and_packaged(demo: tuple[Path, dict]) -> None:
    out, summary = demo
    assert summary["demo"] is True
    assert [b["book_id"] for b in summary["books"]] == [f"DEMO-BOOK-{p.value}" for p in Profile]
    for book in summary["books"]:
        assert book["atoms_committed"] == 16
        assert book["store_verified"] is True
        pkg = load_package(out / f"package-{book['profile']}")
        assert pkg.package_sha256 == book["package"]["package_sha256"]
        assert pkg.demo is True


def test_all_32_messages_of_each_book_last_1_1_to_2_0_s(demo: tuple[Path, dict]) -> None:
    for book in demo[1]["books"]:
        msg = book["messages"]
        assert msg["legal"] == 32
        assert msg["outside_1_1_to_2_0_s"] == 0
        assert (
            MIN_MESSAGE_SAMPLES <= msg["min_samples"] <= msg["max_samples"] <= MAX_MESSAGE_SAMPLES
        )
        assert (MIN_MESSAGE_SAMPLES, MAX_MESSAGE_SAMPLES) == (52_800, 96_000)


def test_leak_scan_finds_no_heldout_message_audio(demo: tuple[Path, dict]) -> None:
    out, summary = demo
    for book in summary["books"]:
        assert book["leak_scan"]["ok"] is True
        assert book["leak_scan"]["heldout_audio"] == 0
        assert book["leak_scan"]["heldout_hashes"] == 14
        assert book["package"]["trained_message_wavs"] == 18
        assert book["package"]["heldout_message_wavs"] == 0
        wavs = list((out / f"package-{book['profile']}" / "messages").glob("*.wav"))
        assert len(wavs) == 18


def test_committed_summary_matches_a_fresh_build(demo: tuple[Path, dict]) -> None:
    out, _ = demo
    assert (out / "summary.json").read_bytes() == tool.SUMMARY.read_bytes()
    assert tool.main(["--check"]) == 0


def test_out_refuses_a_non_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        tool.main(["--out", str(tmp_path)])
