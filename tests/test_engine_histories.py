"""Synthetic engine-produced history checks (#78 AC3/AC4 evidence, not closure).

Unit cases use tiny synthetic record shapes. The optional integration case re-verifies a
retained Unity driver output (`EngineHistoryExportTests`) when its private pins are
supplied; it is skipped, not passed, without them.
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

from tools.mock_visit import engine_histories as eh
from tools.mock_visit.records import EvidenceError

H = {name: (name * 64)[:64] for name in "abcdef"}


def visit(name, items, requests=(), lessons=(), ledger=()):
    records = [{"event_type": "audio_request", "opportunity_id": opp, "payload": {"pcm_sha256": pcm}} for opp, pcm in requests]
    records += [{"event_type": "lesson", "payload": {"pcm_sha256": pcm, "action_pcm_sha256": None, "referent_pcm_sha256": None}} for pcm in lessons]
    return {"visit": name, "items": items, "records": records, "ledger": list(ledger)}


MESSAGES_A = {"K-a1-r2": {"status": "heldout", "composite_sha256": H["a"]}, "K-a1-r1": {"status": "trained", "composite_sha256": H["b"]}}
MESSAGES_B = {"K-a1-r2": {"status": "heldout", "combinations": [{"composite_sha256": H["a"]}, {"composite_sha256": H["c"]}]},
              "K-a1-r1": {"status": "trained", "combinations": [{"composite_sha256": H["b"]}]}}
NOVEL = {"NV-1": {"block": "novel", "message_id": "K-a1-r2"}, "TR-1": {"block": "trained", "message_id": "K-a1-r1"}}


def test_heldout_index_covers_every_b_combination():
    assert eh.heldout_index("A", MESSAGES_A) == {H["a"]: "K-a1-r2"}
    assert eh.heldout_index("B", MESSAGES_B) == {H["a"]: "K-a1-r2", H["c"]: "K-a1-r2"}


def test_scheduled_novel_request_passes_once():
    index = eh.heldout_index("B", MESSAGES_B)
    result = eh.scan_contamination("B", [visit("V1", NOVEL, [("TR-1", H["b"]), ("NV-1", H["c"])], [H["b"]], [{"pcm_sha256": H["d"]}])], index)
    assert result["authorized_novel_requests"] == 1 and result["early_or_repeated_heldout_requests"] == 0


@pytest.mark.parametrize(("history", "code"), [
    ([visit("V1", NOVEL, [("TR-1", H["a"]), ("NV-1", H["a"])])], "ENGINE_HOLDOUT_EARLY_OR_WRONG"),
    ([visit("V1", NOVEL, [("NV-1", H["a"])]), visit("V2", NOVEL, [("NV-1", H["c"])])], "ENGINE_HOLDOUT_REPLAYED"),
    ([visit("V1", NOVEL, [("NV-1", H["a"])], [H["c"]])], "ENGINE_HOLDOUT_IN_LESSON"),
    ([visit("V1", NOVEL, [("NV-1", H["a"])], ledger=[{"pcm_sha256": H["c"]}])], "ENGINE_HOLDOUT_IN_MENU_LEDGER"),
    ([visit("V1", NOVEL, [("TR-1", H["b"])])], "ENGINE_SCHEDULED_NOVEL_MISSING"),
])
def test_contamination_and_missing_novel_are_rejected(history, code):
    with pytest.raises(EvidenceError, match=code):
        eh.scan_contamination("B", history, eh.heldout_index("B", MESSAGES_B))


def play(key, n, candidate, pcm, event, source=None):
    return {"kind": "play_request", "menu_key": key, "presentation_index": n, "candidate_id": candidate, "pcm_sha256": pcm,
            "event_id": event, "yoked_source_event_id": source}


def test_yoked_menu_ledger_must_replay_active_plays_with_source_ids():
    active = [play("K-a1", n, "c1", H["d"], f"e{n}") for n in range(1, 9)]
    yoked = [play("K-a1", n, "c1", H["d"], f"y{n}", f"e{n}") for n in range(1, 9)]
    assert eh.compare_menus(active, yoked) == 8
    with pytest.raises(EvidenceError, match="ENGINE_MENU_YOKED_MISMATCH"):
        eh.compare_menus(active, yoked[:7] + [play("K-a1", 8, "c2", H["e"], "y8", "e8")])
    with pytest.raises(EvidenceError, match="ENGINE_MENU_YOKED_MISMATCH"):
        eh.compare_menus(active, yoked[:7] + [play("K-a1", 8, "c1", H["d"], "y8", "other")])
    with pytest.raises(EvidenceError, match="ENGINE_MENU_PLAY_COUNT"):
        eh.compare_menus(active, yoked[:7])


def test_scanner_from_pr173_is_optional():
    result = eh.optional_scanner("A", [], set())
    if importlib.util.find_spec("tools.mock_visit.holdouts") is None:
        assert result == {"available": False, "note": "tools.mock_visit.holdouts (PR #173) is not on this branch; scan skipped"}
    else:
        assert result["available"] is True and result["issue78_accepted"] is False


@pytest.mark.parametrize("study", ["A", "B"])
def test_retained_engine_history_when_provisioned(study, tmp_path):
    index, fixtures = os.environ.get(f"AV_ENGINE_HISTORY_INDEX_{study}"), os.environ.get("AV_ENGINE_HISTORY_FIXTURES")
    if not index or not fixtures:
        pytest.skip("Provision retained EngineHistoryExportTests output and fixture pins (synthetic DEMO only)")
    pin = Path(index + ".sha256").read_text().strip()
    fixture_pin = Path(fixtures + ".sha256").read_text().strip()
    out = tmp_path / "report.json"
    assert eh.main(["--index", index, "--sha256", pin, "--fixtures", fixtures, "--fixtures-sha256", fixture_pin, "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["synthetic"] is True and report["issue78_accepted"] is False and report["callbacks_recorded"] is False
    assert all(counts == eh.EXPECTED_PLAYS[study] for counts in report["plays_per_person"].values())
    assert len(report["plays_per_person"]) == (1 if study == "A" else 2)
    if study == "B":
        assert report["menu_ledger_pairs"] == {"V1": 72, "V2": 32, "V3": 32}
