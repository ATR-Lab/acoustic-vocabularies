"""Actual store growth and generated history contracts; no acoustic claims."""
import importlib.util
from pathlib import Path
import sys

import pytest

pytest.importorskip("numpy", reason="Producer regressions run in the locked sound environment")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "sound/src"), str(ROOT / "schedules/src")]

from av_schedules.checks import person_findings, person_observations
from av_schedules.design import build_units
from av_schedules.orders import unit_schedules
from av_schedules.seeds import demo_seed
from av_sound import Profile

@pytest.mark.parametrize("profile", list(Profile))
def test_real_store_growth_preserves_bytes_recipes_profile_and_semantics(tmp_path, profile):
    spec = importlib.util.spec_from_file_location("growth", ROOT / "sound/tools/store_growth_demo.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    try:
        report = module.run_book(tmp_path, profile)
        assert [w["n_entries"] for w in report["waves"]] == [8, 12, 16]
        assert all(w["old_entries"] == w["old_unchanged"] and not w["violations"] for w in report["waves"])
        assert report["entries_unchanged_after_attempts"] and report["verify_ok"], report
        assert len(report["overwrite_attempts"]) == 4
        assert all(a["rejected"] and a["logged_event"] == "overwrite_rejected" for a in report["overwrite_attempts"])
    finally:
        module._make_writable(tmp_path)


@pytest.mark.parametrize("study", ["A", "B"])
def test_generated_learner_and_dyad_histories_have_design_counts_and_no_early_holdouts(study):
    master = demo_seed("DEMO-integrity-01")
    unit = build_units(master, study, "confirmatory")[0]
    schedules = unit_schedules(master, unit)
    persons = {}
    for document in schedules.values():
        persons.setdefault(document["person_id"], {})[document["visit"]] = document
    assert len(persons) == (12 if study == "A" else 2)
    for person, docs in persons.items():
        assert not person_findings(study, unit.unit_id, person, docs)
        observations = {key: value for key, _, value in person_observations(study, docs)}
        if study == "A":
            assert observations["teaching_plays_A.atomic"] == 48
            assert observations["teaching_plays_A.whole_phrase"] == 108
        else:
            assert observations["B_atom_menu_plays_per_person"] == 128
            assert observations["B_extra_profile_choice_plays"] == 8
