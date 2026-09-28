from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import uuid

from .agents import AgentResult, DECISION_RANK, Finding, SEVERITY_RANK, rule_based_decision, stricter_decision
from .custom_agent_installer import metadata_dir
from .diff_collector import DiffSummary, collect_diff
from .project_context import collect_project_context
from .quality import QualityCheckResult, run_quality_checks
from .repository import RepositoryContext, resolve_repository
from .review_markdown import render_review_markdown
from .secrets import SecretScanResult, scan_diff_for_secrets


ALLOWED_TARGETS = {"base", "staged", "uncommitted", "commits", "file"}
VALID_STATUSES = {"completed", "inconclusive", "blocked", "failed"}
INCOMPLETE_REVIEWER_STATES = {
    "failed",
    "missing",
    "not_run",
    "blocked",
    "inconclusive",
    "cancelled",
    "pending",
    "running",
    "delegated",
}
MAX_PREPARED_CONTEXTS = 32


class ReviewMcpError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedReview:
    context_id: str
    repository: RepositoryContext
    target: str
    diff: DiffSummary
    secret_scan: SecretScanResult
    quality_checks: list[QualityCheckResult]
    project_context: dict[str, object]


_PREPARED: OrderedDict[str, PreparedReview] = OrderedDict()


def prepare_review_context(
    repository_path: str = ".",
    *,
    target: str = "base",
    base_branch: str | None = None,
    commits: str | None = None,
    file_path: str | None = None,
    run_quality: bool = True,
) -> dict[str, object]:
    if target not in ALLOWED_TARGETS:
        allowed = ", ".join(sorted(ALLOWED_TARGETS))
        raise ReviewMcpError(f"未対応のreview targetです: {target}. Expected: {allowed}")

    repository = resolve_repository(repository_path, base_branch=base_branch)
    diff = collect_diff(
        repository,
        target=target,
        commits=commits,
        file_path=file_path,
    )
    secret_scan = scan_diff_for_secrets(diff)

    secret_findings = [
        {
            "category": item.category,
            "classification": item.classification,
            "source": item.source,
            "file": item.file,
            "line": item.line,
            "fingerprint": item.fingerprint,
        }
        for item in secret_scan.findings
    ]

    if secret_scan.blocked:
        return {
            "status": "blocked",
            "reason": "confirmed secret候補を検出したため、diffをAIへ渡しません。",
            "review_context_id": None,
            "review_target": target,
            "repository": _repository_payload(repository),
            "changed_files": [asdict(item) for item in diff.changed_files],
            "changed_file_count": diff.changed_file_count,
            "diff_line_count": diff.diff_line_count,
            "truncation_status": "truncated" if diff.truncated else "complete",
            "truncation_reason": diff.truncation_reason,
            "secret_scan_status": "blocked",
            "secret_findings": secret_findings,
            "quality_check_status": "not_run",
            "quality_checks": [],
            "requirements_context": [],
            "design_context": [],
            "project_rules": [],
            "project_context_warnings": [],
            "warnings": list(diff.warnings),
        }

    project_context = collect_project_context(repository.root)
    quality_checks = run_quality_checks(repository.root) if run_quality else [
        QualityCheckResult(name="quality", command=[], status="skipped")
    ]
    context_id = uuid.uuid4().hex
    prepared = PreparedReview(
        context_id=context_id,
        repository=repository,
        target=target,
        diff=diff,
        secret_scan=secret_scan,
        quality_checks=quality_checks,
        project_context=project_context,
    )
    _store_context(prepared)

    return {
        "status": "ready",
        "review_context_id": context_id,
        "review_target": target,
        "repository": _repository_payload(repository),
        "base_ref": repository.base_branch,
        "head_ref": repository.head_sha,
        "changed_files": [asdict(item) for item in diff.changed_files],
        "changed_file_count": diff.changed_file_count,
        "diff_line_count": diff.diff_line_count,
        "diff": diff.diff_text,
        "truncation_status": "truncated" if diff.truncated else "complete",
        "truncation_reason": diff.truncation_reason,
        "secret_scan_status": "passed",
        "secret_findings": secret_findings,
        "quality_check_status": _quality_summary(quality_checks),
        "quality_checks": [_quality_payload(item) for item in quality_checks],
        "requirements_context": project_context.get("requirements", []),
        "design_context": project_context.get("design_context", []),
        "project_rules": project_context.get("project_rules", []),
        "project_context_warnings": project_context.get("warnings", []),
        "warnings": list(diff.warnings),
    }


def finalize_review_context(
    review_context_id: str,
    final_result: dict[str, object],
) -> dict[str, object]:
    prepared = _PREPARED.get(review_context_id)
    if prepared is None:
        raise ReviewMcpError(
            "review_context_idが見つかりません。prepare_reviewからやり直してください。"
        )

    result = _parse_final_result(final_result, review_context_id)
    states = result.reviewer_states or {}
    quality_failed = any(item.status == "failed" for item in prepared.quality_checks)
    incomplete = (
        result.status != "completed"
        or not states
        or any(state in INCOMPLETE_REVIEWER_STATES for state in states.values())
        or quality_failed
    )

    deterministic = rule_based_decision(
        [result],
        truncated=prepared.diff.truncated,
        failed=incomplete,
    )
    if prepared.secret_scan.blocked:
        deterministic = "BLOCKED"

    final_decision = stricter_decision(deterministic, result.decision)
    report_path = _write_report(prepared, result, final_decision)
    _PREPARED.pop(review_context_id, None)

    counts = {severity: 0 for severity in SEVERITY_RANK}
    for finding in result.findings:
        if finding.severity in counts:
            counts[finding.severity] += 1

    return {
        "status": "completed",
        "decision": final_decision,
        "ai_decision": result.decision,
        "deterministic_decision": deterministic,
        "incomplete_review": incomplete,
        "summary": result.summary,
        "finding_counts": counts,
        "human_check_count": len(result.human_checks or []),
        "report_path": str(report_path),
    }


def clear_prepared_contexts() -> None:
    _PREPARED.clear()


def _store_context(prepared: PreparedReview) -> None:
    _PREPARED[prepared.context_id] = prepared
    _PREPARED.move_to_end(prepared.context_id)
    while len(_PREPARED) > MAX_PREPARED_CONTEXTS:
        _PREPARED.popitem(last=False)


def _repository_payload(repository: RepositoryContext) -> dict[str, object]:
    return {
        "root": str(repository.root),
        "remote_url": repository.remote_url,
        "current_branch": repository.current_branch,
        "head_sha": repository.head_sha,
        "base_branch": repository.base_branch,
        "project_id": repository.project_id,
    }


def _quality_payload(item: QualityCheckResult) -> dict[str, object]:
    return {
        "name": item.name,
        "command": item.command,
        "status": item.status,
        "returncode": item.returncode,
    }


def _quality_summary(items: list[QualityCheckResult]) -> str:
    if any(item.status == "failed" for item in items):
        return "failed"
    if items and all(item.status == "skipped" for item in items):
        return "skipped"
    if any(item.status == "passed" for item in items):
        return "passed"
    return "unknown"


def _parse_final_result(payload: dict[str, object], context_id: str) -> AgentResult:
    if not isinstance(payload, dict):
        raise ReviewMcpError("final_resultはobjectである必要があります。")

    agent = payload.get("agent")
    if agent != "final":
        raise ReviewMcpError("final_result.agentは 'final' である必要があります。")

    decision = payload.get("decision")
    if not isinstance(decision, str) or decision not in DECISION_RANK:
        raise ReviewMcpError("final_result.decisionが不正です。")

    status = payload.get("status", "completed")
    if not isinstance(status, str) or status not in VALID_STATUSES:
        raise ReviewMcpError("final_result.statusが不正です。")

    summary = payload.get("summary")
    if not isinstance(summary, str):
        raise ReviewMcpError("final_result.summaryはstringである必要があります。")

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise ReviewMcpError("final_result.findingsはarrayである必要があります。")
    findings = [_parse_finding(item) for item in raw_findings]

    reviewer_states = _optional_string_map(payload.get("reviewer_states"), "reviewer_states")
    conflicts = _optional_dict_list(payload.get("conflicts"), "conflicts")
    human_checks = _optional_dict_list(payload.get("human_checks"), "human_checks")
    challenge_decisions = _optional_dict_list(
        payload.get("challenge_decisions"),
        "challenge_decisions",
    )
    excluded_findings = _optional_dict_list(
        payload.get("excluded_findings"),
        "excluded_findings",
    )
    review_coverage = payload.get("review_coverage")
    if review_coverage is not None and not isinstance(review_coverage, dict):
        raise ReviewMcpError("review_coverageはobjectまたはnullである必要があります。")

    incomplete_review = payload.get("incomplete_review")
    if incomplete_review is not None and not isinstance(incomplete_review, bool):
        raise ReviewMcpError("incomplete_reviewはbooleanまたはnullである必要があります。")

    return AgentResult(
        run_id=context_id,
        agent="final",
        provider="github-copilot-chat",
        schema_version="0.1.0",
        decision=decision,
        findings=findings,
        summary=summary,
        status=status,
        reviewer_states=reviewer_states,
        conflicts=conflicts,
        incomplete_review=incomplete_review,
        human_checks=human_checks,
        challenge_decisions=challenge_decisions,
        excluded_findings=excluded_findings,
        review_coverage=dict(review_coverage) if isinstance(review_coverage, dict) else None,
    )


def _parse_finding(value: object) -> Finding:
    if not isinstance(value, dict):
        raise ReviewMcpError("findingはobjectである必要があります。")

    severity = value.get("severity")
    message = value.get("message")
    if not isinstance(severity, str) or severity not in SEVERITY_RANK:
        raise ReviewMcpError("finding.severityが不正です。")
    if not isinstance(message, str) or not message.strip():
        raise ReviewMcpError("finding.messageは空でないstringである必要があります。")

    category = _optional_string(value.get("category"), "finding.category")
    file_value = _optional_string(value.get("file"), "finding.file")
    line = value.get("line")
    if line is not None and (not isinstance(line, int) or isinstance(line, bool)):
        raise ReviewMcpError("finding.lineはintegerまたはnullである必要があります。")
    range_value = _optional_string(value.get("range"), "finding.range")
    line_range = _optional_string(
        value.get("line_range", value.get("line/range")),
        "finding.line_range",
    )
    rationale = _optional_string(value.get("rationale"), "finding.rationale")
    recommendation = _optional_string(
        value.get("recommendation"),
        "finding.recommendation",
    )
    confidence = _optional_string(value.get("confidence"), "finding.confidence")
    reported_by = _optional_string_list(value.get("reported_by"), "finding.reported_by")
    reported_severities = _optional_string_list(
        value.get("reported_severities"),
        "finding.reported_severities",
    )
    if reported_severities is not None and any(
        item not in SEVERITY_RANK for item in reported_severities
    ):
        raise ReviewMcpError("finding.reported_severitiesが不正です。")
    severity_conflict = value.get("severity_conflict")
    if severity_conflict is not None and not isinstance(severity_conflict, bool):
        raise ReviewMcpError("finding.severity_conflictはbooleanまたはnullである必要があります。")

    return Finding(
        severity=severity,
        message=message,
        file=file_value,
        line=line,
        category=category,
        range=range_value,
        line_range=line_range,
        rationale=rationale,
        recommendation=recommendation,
        confidence=confidence,
        reported_by=reported_by,
        reported_severities=reported_severities,
        severity_conflict=severity_conflict,
    )


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReviewMcpError(f"{field}はstringまたはnullである必要があります。")
    return value


def _optional_string_list(value: object, field: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ReviewMcpError(f"{field}はstring arrayまたはnullである必要があります。")
    return list(value)


def _optional_string_map(value: object, field: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise ReviewMcpError(f"{field}はstring mapまたはnullである必要があります。")
    return dict(value)


def _optional_dict_list(value: object, field: str) -> list[dict[str, object]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ReviewMcpError(f"{field}はobject arrayまたはnullである必要があります。")
    return [dict(item) for item in value]


def _write_report(
    prepared: PreparedReview,
    result: AgentResult,
    final_decision: str,
) -> Path:
    project_dir = metadata_dir() / "reviews" / prepared.repository.project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / "review.md"
    markdown = render_review_markdown(
        project_id=prepared.repository.project_id,
        target=prepared.target,
        final_decision=final_decision,
        final_result=result,
        changed_file_count=prepared.diff.changed_file_count,
        diff_line_count=prepared.diff.diff_line_count,
        truncated=prepared.diff.truncated,
        reviewer_states=result.reviewer_states,
        run_id=None,
        execution_mode="copilot-chat-mcp",
        execution_strategy="native-subagent",
    )
    path.write_text(markdown, encoding="utf-8")
    return path
