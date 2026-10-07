"""#34 shared contracts: GLMM ladder order, section 9 report order, table statements."""

from __future__ import annotations

from av_analysis.glmm import LADDER, GlmmLog, LadderAttempt, ladder_order_ok
from av_analysis.report import REPORT_SECTIONS, TableMeta, sections_for


def attempt(rung, status):
    return LadderAttempt(rung, None, status, None, None)


def test_ladder_follows_the_analysis_plan_order():
    assert LADDER == (
        "full",
        "no_correlations",
        "no_dyad_role_slope",
        "no_participant_teaching_slope",
        "descriptive",
    )


def test_ladder_order_check():
    assert ladder_order_ok([attempt("full", "accepted")])
    assert ladder_order_ok(
        [
            attempt("full", "failed"),
            attempt("no_correlations", "failed"),
            attempt("no_dyad_role_slope", "skipped"),
            attempt("no_participant_teaching_slope", "accepted"),
        ]
    )
    assert ladder_order_ok(
        [attempt(r, "failed") for r in LADDER[:4]] + [attempt("descriptive", "skipped")]
    )
    assert not ladder_order_ok([])
    assert not ladder_order_ok([attempt("no_correlations", "accepted")])  # skipped the full model
    assert not ladder_order_ok([attempt("full", "accepted"), attempt("no_correlations", "failed")])
    assert not ladder_order_ok([attempt("full", "failed")])  # stops without a stable model


def test_final_rung_defaults_to_descriptive():
    log = GlmmLog("SYNTHETIC", "A", "A-trained-D0", None, "0" * 64, (attempt("full", "failed"),))
    assert log.final_rung == "descriptive"
    assert log.document()["engine"] is None


def test_report_sections_in_section_9_order():
    assert [s.number for s in REPORT_SECTIONS] == list(range(1, 9))
    assert [s.id for s in REPORT_SECTIONS] == [
        "flow",
        "fidelity",
        "a_primary",
        "b_primary",
        "secondary",
        "ownership_consultation",
        "sensitivities",
        "deviations",
    ]
    assert [s.id for s in sections_for("A")] == [
        s.id for s in REPORT_SECTIONS if s.id != "b_primary"
    ]
    assert "a_primary" not in [s.id for s in sections_for("B")]


def test_table_meta_states_units_trials_and_missing():
    meta = TableMeta("batch", 17, 18, 7344, 1, "complete batch differences")
    assert meta.line() == (
        "Independent unit: batch; 17 of 18 planned units contribute (1 missing); 7344 trials. "
        "complete batch differences"
    )
    assert TableMeta("dyad", 64, 64, 0, 0).line().endswith("0 trials.")
