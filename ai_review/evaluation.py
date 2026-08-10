from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Literal

from .agents import SEVERITY_RANK


ExecutionStrategy = Literal["sequential", "limited_parallel", "native"]
ManualStatus = Literal["PASS", "FAIL", "PARTIAL", "BLOCKED", "NOT_OBSERVABLE"]

VALID_EXECUTION_STRATEGIES: set[str] = {"sequential", "limited_parallel", "native"}
DEFAULT_STANDARD_STRATEGY: ExecutionStrategy = "native"
DEFAULT_MAX_PARALLEL_REVIEWERS = 2
SPECIALIST_REVIEWER_COUNT = 8


@dataclass(frozen=True)
class ExpectedFinding:
    concept: str
    category: str
    severity: str
    file: str | None = None


@dataclass(frozen=True)
class ActualFinding:
    message: str
    category: str | None = None
    severity: str = "Info"
    file: str | None = None
    line: int | None = None
    reported_by: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewerTiming:
    status: str
    duration_ms: int | None = None


@dataclass(frozen=True)
class CreditsObservation:
    available: bool
    value: float | None = None
    source: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class RateLimitObservation:
    observed: bool
    observable: bool
    retry_after_seconds: int | None = None
    converted_to_failure: bool | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ChatUiObservation:
    reviewer_name_visible: ManualStatus
    status_visible: ManualStatus
    prompt_context_visible: ManualStatus
    tool_usage_visible: ManualStatus
    returned_result_visible: ManualStatus
    notes: str | None = None


@dataclass(frozen=True)
class FinalReviewerQuality:
    duplicate_merge: ManualStatus
    provenance_preserved: ManualStatus
    severity_conflict_handled: ManualStatus
    incomplete_state_visible: ManualStatus
    failed_reviewer_prevents_approve: ManualStatus
    findings_preserved: ManualStatus


@dataclass(frozen=True)
class ReportConsistency:
    run_id: ManualStatus
    execution_strategy: ManualStatus
    reviewer_states: ManualStatus
    final_decision: ManualStatus
    findings: ManualStatus
    incomplete_review: ManualStatus
    timing: ManualStatus


@dataclass(frozen=True)
class EvaluationMetrics:
    critical_major_recall: float | None
    false_positive_count: int
    duplicate_finding_count: int


@dataclass(frozen=True)
class EvaluationResult:
    scenario: str
    execution_strategy: ExecutionStrategy
    started_at: str
    finished_at: str
    duration_ms: int | None
    orchestrator_duration_ms: int | None
    final_reviewer_duration_ms: int | None
    reviewers: dict[str, ReviewerTiming]
    expected_findings: list[ExpectedFinding]
    actual_findings: list[ActualFinding]
    metrics: EvaluationMetrics
    credits: CreditsObservation
    rate_limit: RateLimitObservation
    final_decision: str
    chat_ui: ChatUiObservation | None = None
    final_reviewer_quality: FinalReviewerQuality | None = None
    report_consistency: ReportConsistency | None = None
    manual_status: ManualStatus | None = None
    notes: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_execution_strategy(strategy: str) -> ExecutionStrategy:
    if strategy not in VALID_EXECUTION_STRATEGIES:
        allowed = ", ".join(sorted(VALID_EXECUTION_STRATEGIES))
        raise ValueError(f"invalid execution strategy: {strategy}. Expected one of: {allowed}")
    return strategy  # type: ignore[return-value]


def validate_max_parallel_reviewers(value: int) -> int:
    if value < 1:
        raise ValueError("max_parallel_reviewers must be >= 1")
    if value > SPECIALIST_REVIEWER_COUNT:
        raise ValueError(f"max_parallel_reviewers must be <= {SPECIALIST_REVIEWER_COUNT}")
    return value


def compute_metrics(
    expected_findings: list[ExpectedFinding],
    actual_findings: list[ActualFinding],
) -> EvaluationMetrics:
    expected_major = [item for item in expected_findings if _is_major_or_critical(item.severity)]
    matched_expected = sum(1 for item in expected_major if _matches_expected(item, actual_findings))
    recall = None if not expected_major else matched_expected / len(expected_major)
    false_positives = sum(
        1
        for item in actual_findings
        if _is_major_or_critical(item.severity) and not any(_matches_actual(expected, item) for expected in expected_findings)
    )
    duplicates = _duplicate_count(actual_findings)
    return EvaluationMetrics(
        critical_major_recall=recall,
        false_positive_count=false_positives,
        duplicate_finding_count=duplicates,
    )


def evaluation_result_to_dict(result: EvaluationResult) -> dict[str, object]:
    return asdict(result)


def save_evaluation_result(path: Path, result: EvaluationResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evaluation_result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")


def render_strategy_table(results: list[EvaluationResult]) -> str:
    lines = [
        "Strategy          Quality    Duration    Credits       Rate limit",
        "---------------------------------------------------------------",
    ]
    by_strategy: dict[str, list[EvaluationResult]] = {}
    for result in results:
        by_strategy.setdefault(result.execution_strategy, []).append(result)

    for strategy in ("sequential", "limited_parallel", "native"):
        strategy_results = by_strategy.get(strategy, [])
        if not strategy_results:
            lines.append(f"{strategy:<17} UNAVAILABLE UNAVAILABLE Unavailable   NOT_OBSERVABLE")
            continue
        recall_values = [
            item.metrics.critical_major_recall
            for item in strategy_results
            if item.metrics.critical_major_recall is not None
        ]
        quality = "UNAVAILABLE" if not recall_values else f"recall={sum(recall_values) / len(recall_values):.2f}"
        durations = [item.duration_ms for item in strategy_results if item.duration_ms is not None]
        duration = "UNAVAILABLE" if not durations else f"{sum(durations) // len(durations)}ms"
        credits = "Unavailable" if not all(item.credits.available for item in strategy_results) else "Available"
        rate_limit = "observed" if any(item.rate_limit.observed for item in strategy_results) else "not observed"
        lines.append(f"{strategy:<17} {quality:<10} {duration:<11} {credits:<11} {rate_limit}")
    return "\n".join(lines)


def default_credits_unavailable() -> CreditsObservation:
    return CreditsObservation(
        available=False,
        value=None,
        source=None,
        reason="Current GitHub Copilot interfaces do not expose per-subagent credit usage.",
    )


def _is_major_or_critical(severity: str) -> bool:
    return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK["Major"]


def _matches_expected(expected: ExpectedFinding, actual_findings: list[ActualFinding]) -> bool:
    return any(_matches_actual(expected, actual) for actual in actual_findings)


def _matches_actual(expected: ExpectedFinding, actual: ActualFinding) -> bool:
    if not _is_major_or_critical(actual.severity):
        return False
    if expected.category and actual.category and expected.category != actual.category:
        return False
    if expected.file and actual.file and Path(expected.file).as_posix() != Path(actual.file).as_posix():
        return False
    expected_terms = _normalize_terms(expected.concept)
    actual_terms = _normalize_terms(" ".join(filter(None, [actual.message, actual.category or "", actual.file or ""])))
    return bool(expected_terms & actual_terms)


def _duplicate_count(findings: list[ActualFinding]) -> int:
    seen: set[tuple[str | None, str | None, str]] = set()
    duplicates = 0
    for finding in findings:
        key = (
            finding.category,
            Path(finding.file).as_posix() if finding.file else None,
            " ".join(sorted(_normalize_terms(finding.message)))[:120],
        )
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _normalize_terms(text: str) -> set[str]:
    separators = "/\\:;,.()[]{}-_`'\""
    normalized = text.lower()
    for char in separators:
        normalized = normalized.replace(char, " ")
    return {term for term in normalized.split() if len(term) >= 4}
