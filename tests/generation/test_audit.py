"""Audit table contract with the analysis pipeline (#24 owns this module and its schema).

Skeleton tests: the column contract, the masked-column rule and `audit-summary.schema.json`.
#24 extends this module with the audit implementation tests.
"""

from av_generation import audit
from av_generation._schemas import schema_errors
from av_generation.masking import masking_findings
from av_generation.outcomes import LLM_ONLY_OUTCOMES, OUTCOME_CODES


def test_audit_columns():
    assert tuple(f"n_{c}" for c in OUTCOME_CODES) == audit.OUTCOME_COLUMNS
    assert len(set(audit.BOOK_COLUMNS)) == len(audit.BOOK_COLUMNS)
    assert set(audit.BOOK_COLUMNS) >= audit.METHOD_COLUMNS
    assert not set(audit.MASKED_BOOK_COLUMNS) & audit.METHOD_COLUMNS
    assert audit.SET_AUDIT_NAME.format(study="A", set="confirmatory") == (
        "A-confirmatory-audit.csv"
    )


def test_masked_columns_reveal_no_method_by_construction():
    masked = set(audit.MASKED_BOOK_COLUMNS)
    assert not {f"n_{o.value}" for o in LLM_ONLY_OUTCOMES} & masked
    assert not {c for c in masked if c.startswith("n_")}
    per_method_effort = {
        "startup_ms",
        "operator_ms",
        "design_active_ms",
        "familiarization_ms",
        "model_runtime_ms",
        "tokens_in",
        "tokens_out",
        "n_llm_server_error",
    }
    assert not per_method_effort & masked
    assert masking_findings(",".join(audit.MASKED_BOOK_COLUMNS)) == ()
    assert {"slots_valid", "slots_invalid", "failed_generation", "nonfallback"} <= masked


def _book_row(masked: bool) -> dict:
    row = {c: 0 for c in audit.BOOK_COLUMNS}
    row.update(
        batch_id="DEMO-A-P01",
        book_id="DEMO-BK-H9TC",
        profile="P1",
        failed_generation=False,
        nonfallback=True,
        method="A3",
        designer_id=None,
        candidate_diversity=0.31,
        committed_diversity=None,
        wall_ms=None,
        startup_ms=None,
    )
    if masked:
        row = {k: v for k, v in row.items() if k not in audit.METHOD_COLUMNS}
    return row


def _summary(masked: bool, row: dict) -> dict:
    return {
        "format": "av-generation/audit-summary",
        "format_version": 1,
        "masked": masked,
        "run_id": "DEMO-run-01",
        "batch_id": "DEMO-A-P01",
        "sources": {"logs/slots.jsonl": "a" * 64},
        "books": [row],
        "timing": {
            "batch_wall_ms": None,
            "appointments": [],
            "atoms": [],
            "max_atom_ms": None,
            "max_appointment_ms": None,
        },
    }


def test_audit_summary_masking_rule():
    assert schema_errors("audit-summary.schema.json", _summary(False, _book_row(False))) == ()
    assert schema_errors("audit-summary.schema.json", _summary(True, _book_row(True))) == ()
    assert schema_errors("audit-summary.schema.json", _summary(True, _book_row(False)))
    assert schema_errors("audit-summary.schema.json", _summary(False, _book_row(True)))
    leaky = dict(_book_row(True), n_overflow_input=0)
    assert schema_errors("audit-summary.schema.json", _summary(True, leaky))
    assert tuple(_book_row(False)) == audit.BOOK_COLUMNS
    assert tuple(_book_row(True)) == audit.MASKED_BOOK_COLUMNS
