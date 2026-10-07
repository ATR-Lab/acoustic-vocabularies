"""Masking deny lists: no outcome by condition in reconciled outputs or the dashboard."""

from __future__ import annotations

import pytest

from av_analysis.derived import TABLES
from av_analysis.masking import (
    forbidden_columns,
    forbidden_keys,
    forbidden_reason,
    free_text_columns,
)
from av_analysis.templates import TEMPLATES

# The fixture columns of the #35 allowlist acceptance test.
ISSUE_35_FIXTURE = (
    "exact_correct",
    "response_action",
    "response_time_ms",
    "method_masked",
    "role",
    "scaffold_family",
)


def test_issue_35_fixture_columns_are_all_forbidden_when_masked():
    reasons = forbidden_columns(ISSUE_35_FIXTURE, "masked")
    assert set(reasons) == set(ISSUE_35_FIXTURE)
    assert reasons["exact_correct"] == "outcome"
    assert reasons["role"] == "condition"


@pytest.mark.parametrize(
    ("field", "masked", "derived"),
    [
        ("exact_correct", "outcome", None),
        ("person_accuracy", "outcome", None),
        ("rt_ms", "outcome", None),
        ("response_code", "response", None),
        ("target_referent", "hidden_answer", None),
        ("ownership_rating", "rating", None),
        ("method", "condition", "condition"),
        ("structured_family", "condition", "condition"),
        ("presentation", "condition", "condition"),
        ("yoked_source_event_id", "condition", "condition"),
        ("first_name", "personal", "personal"),
        ("contact_email", "personal", "personal"),
        ("participant_name", "personal", "personal"),
        ("operator", "personal", "personal"),
        ("reviewer", "personal", "personal"),
        ("operator_signoff", "personal", "personal"),
        ("name", None, None),
        ("fault_missing_response_log_n", None, None),
        ("presentation_index", None, None),
        ("dictionary_available", None, None),
        ("visit_id", None, None),
        ("enrollment_target_n", None, None),
    ],
)
def test_forbidden_reason(field, masked, derived):
    assert forbidden_reason(field, "masked") == masked
    assert forbidden_reason(field, "derived") == derived


def test_every_outcome_response_or_condition_template_column_is_forbidden_when_masked():
    classes = {"outcome", "response", "hidden_answer", "condition", "staff"}
    from av_analysis.templates import COLUMN_CLASS

    for t in TEMPLATES.values():
        for c in t.columns:
            if COLUMN_CLASS[c] in classes:
                assert forbidden_reason(c, "masked") is not None, c


def test_table_specs_respect_their_policy():
    for spec in TABLES.values():
        assert forbidden_columns(spec.header, spec.policy) == {}, spec.name
        if spec.area == "reconciled":
            assert spec.policy == "masked", spec.name
    assert TABLES["trials"].policy == "derived"
    assert "exact_correct" in TABLES["trials"].header  # outcomes allowed in derived only


def test_forbidden_keys_walks_nested_documents():
    doc = {
        "checks": [{"check": "C1", "status": "pass"}],
        "summary": {"by_method": {"A3": 1}, "items": [{"exact_correct": True}]},
    }
    assert forbidden_keys(doc, "masked") == {
        "$.summary.by_method": "condition",
        "$.summary.items[0].exact_correct": "outcome",
    }
    assert forbidden_keys([1, "x"], "masked") == {}


def test_free_text_columns():
    assert free_text_columns() == {"deviations", "observed_problem", "action_taken", "resolution"}
