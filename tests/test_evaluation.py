from __future__ import annotations

from pathlib import Path
import json

import pytest

from ai_review.evaluation import (
    ActualFinding,
    EvaluationResult,
    ExpectedFinding,
    RateLimitObservation,
    ReviewerTiming,
    ChatUiObservation,
    FinalReviewerQuality,
    ReportConsistency,
    compute_metrics,
    default_credits_unavailable,
    render_strategy_table,
    save_evaluation_result,
    utc_now_iso,
    validate_execution_strategy,
    validate_max_parallel_reviewers,
)


def test_evaluation_result_schema_round_trips(tmp_path: Path) -> None:
    expected = [ExpectedFinding(concept="command injection", category="security", severity="Major", file="app.py")]
    actual = [
        ActualFinding(
            message="Possible command injection",
            category="security",
            severity="Major",
            file="app.py",
            reported_by=["Security Reviewer"],
        )
    ]
    result = EvaluationResult(
        scenario="security-vulnerability",
        execution_strategy="sequential",
        started_at=utc_now_iso(),
        finished_at=utc_now_iso(),
        duration_ms=123,
        orchestrator_duration_ms=100,
        final_reviewer_duration_ms=23,
        reviewers={"Security Reviewer": ReviewerTiming(status="completed", duration_ms=100)},
        expected_findings=expected,
        actual_findings=actual,
        metrics=compute_metrics(expected, actual),
        credits=default_credits_unavailable(),
        rate_limit=RateLimitObservation(observed=False, observable=True),
        final_decision="CHANGES_REQUIRED",
        chat_ui=ChatUiObservation("PARTIAL", "PARTIAL", "NOT_OBSERVABLE", "NOT_OBSERVABLE", "PARTIAL"),
        final_reviewer_quality=FinalReviewerQuality("PASS", "PASS", "PARTIAL", "PASS", "PASS", "PASS"),
        report_consistency=ReportConsistency("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS"),
        manual_status="PARTIAL",
    )
    path = tmp_path / "evaluation.json"

    save_evaluation_result(path, result)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["scenario"] == "security-vulnerability"
    assert payload["metrics"]["critical_major_recall"] == 1.0
    assert payload["credits"]["available"] is False


def test_quality_metrics() -> None:
    expected = [
        ExpectedFinding("command injection", "security", "Major", "src/app.py"),
        ExpectedFinding("missing authorization", "security", "Critical", "src/auth.py"),
    ]
    actual = [
        ActualFinding("command injection via shell", "security", "Major", "src/app.py"),
        ActualFinding("command injection via shell", "security", "Major", "src/app.py"),
        ActualFinding("unrelated critical issue", "security", "Critical", "src/other.py"),
        ActualFinding("nit", "maintainability", "Minor", "src/app.py"),
    ]

    metrics = compute_metrics(expected, actual)

    assert metrics.critical_major_recall == 0.5
    assert metrics.false_positive_count == 1
    assert metrics.duplicate_finding_count == 1


def test_quality_metrics_require_specific_concept_and_file_match() -> None:
    expected = [ExpectedFinding("missing authorization", "security", "Major", "src/auth.py")]
    actual = [
        ActualFinding("missing regression test", "security", "Major", "src/auth.py"),
        ActualFinding("missing authorization check", "security", "Major"),
        ActualFinding("missing authorization check", "testing", "Major", "src/auth.py"),
    ]

    metrics = compute_metrics(expected, actual)

    assert metrics.critical_major_recall == 0.0
    assert metrics.false_positive_count == 3


def test_strategy_validation() -> None:
    assert validate_execution_strategy("native") == "native"
    assert validate_execution_strategy("limited_parallel") == "limited_parallel"
    assert validate_max_parallel_reviewers(2) == 2
    with pytest.raises(ValueError):
        validate_execution_strategy("parallel")
    with pytest.raises(ValueError):
        validate_max_parallel_reviewers(0)


def test_strategy_table_uses_unavailable_for_missing_data() -> None:
    table = render_strategy_table([])

    assert "sequential" in table
    assert "limited_parallel" in table
    assert "native" in table
    assert "Unavailable" in table
