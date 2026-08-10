from __future__ import annotations

from dataclasses import dataclass


AGENT_ORDER = [
    "requirements",
    "correctness",
    "security",
    "testing",
    "maintainability",
    "performance",
    "operations",
    "devil_advocate",
    "final",
]


SEVERITY_RANK = {
    "Info": 0,
    "Minor": 1,
    "Major": 2,
    "Critical": 3,
}


DECISION_RANK = {
    "APPROVE": 0,
    "APPROVE_WITH_NOTES": 1,
    "CHANGES_REQUIRED": 2,
    "INCONCLUSIVE": 3,
    "BLOCKED": 4,
}


@dataclass(frozen=True)
class Finding:
    severity: str
    message: str
    file: str | None = None
    line: int | None = None
    category: str | None = None
    range: str | None = None
    line_range: str | None = None
    rationale: str | None = None
    recommendation: str | None = None
    confidence: str | None = None
    reported_by: list[str] | None = None
    reported_severities: list[str] | None = None
    severity_conflict: bool | None = None


@dataclass(frozen=True)
class AgentResult:
    run_id: str
    agent: str
    provider: str
    schema_version: str
    decision: str
    findings: list[Finding]
    summary: str
    status: str = "completed"
    reviewer_states: dict[str, str] | None = None
    conflicts: list[dict[str, object]] | None = None
    incomplete_review: bool | None = None


def rule_based_decision(results: list[AgentResult], *, truncated: bool = False, failed: bool = False) -> str:
    if failed or truncated:
        return "INCONCLUSIVE"
    worst_severity = 0
    for result in results:
        if result.decision == "BLOCKED":
            return "BLOCKED"
        for finding in result.findings:
            worst_severity = max(worst_severity, SEVERITY_RANK.get(finding.severity, 0))
    if worst_severity >= SEVERITY_RANK["Critical"]:
        return "BLOCKED"
    if worst_severity >= SEVERITY_RANK["Major"]:
        return "CHANGES_REQUIRED"
    if worst_severity >= SEVERITY_RANK["Minor"]:
        return "APPROVE_WITH_NOTES"
    return "APPROVE"


def stricter_decision(left: str, right: str) -> str:
    return left if DECISION_RANK[left] >= DECISION_RANK[right] else right
