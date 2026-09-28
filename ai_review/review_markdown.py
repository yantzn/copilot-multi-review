from __future__ import annotations

import json
from typing import Any

from .agents import AgentResult, Finding


SEVERITY_LABELS = {
    "Critical": "致命的",
    "Major": "重大",
    "Minor": "軽微",
    "Info": "情報",
}

CATEGORY_LABELS = {
    "requirements": "要件",
    "correctness": "正当性",
    "design_conformance": "設計整合性",
    "project_rules": "プロジェクトルール",
    "security": "セキュリティ",
    "testing": "テスト",
    "maintainability": "保守性",
    "performance": "性能",
    "operations": "運用",
}

DECISION_LABELS = {
    "APPROVE": "承認可",
    "APPROVE_WITH_NOTES": "注記あり",
    "CHANGES_REQUIRED": "修正必要",
    "BLOCKED": "ブロック",
    "INCONCLUSIVE": "判定不能",
}

STATUS_LABELS = {
    "pending": "待機",
    "running": "実行中",
    "completed": "完了",
    "failed": "失敗",
    "blocked": "ブロック",
    "inconclusive": "判定不能",
    "missing": "欠落",
    "skipped": "スキップ",
    "not_run": "未実行",
    "cancelled": "キャンセル",
}

CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}


def render_review_markdown(
    *,
    project_id: str,
    target: str,
    final_decision: str,
    final_result: AgentResult | None,
    changed_file_count: int,
    diff_line_count: int,
    truncated: bool,
    reviewer_states: dict[str, str] | None = None,
    run_id: str | None = None,
    execution_mode: str | None = None,
    execution_strategy: str | None = None,
) -> str:
    findings = list(final_result.findings) if final_result else []
    states = (final_result.reviewer_states if final_result else None) or reviewer_states or {}
    counts = {severity: 0 for severity in SEVERITY_LABELS}
    for finding in findings:
        if finding.severity in counts:
            counts[finding.severity] += 1

    lines = [
        "# コードレビュー結果",
        "",
        "## レビュー概要",
        "",
        "| 項目 | 内容 |",
        "|---|---|",
        f"| 対象 | {_table(target)} |",
        f"| 最終判定 | {_table(_decision(final_decision))} |",
        f"| 変更ファイル数 | {changed_file_count} |",
        f"| 差分行数 | {diff_line_count} |",
        f"| 差分切り捨て | {'あり' if truncated else 'なし'} |",
        f"| 致命的 | {counts['Critical']} |",
        f"| 重大 | {counts['Major']} |",
        f"| 軽微 | {counts['Minor']} |",
        f"| 情報 | {counts['Info']} |",
        f"| Human Check | {len(final_result.human_checks or []) if final_result else 0} |",
        "",
    ]

    if final_result and final_result.summary:
        lines.extend(["## 総評", "", final_result.summary.strip(), ""])

    lines.extend(["## 指摘一覧", ""])
    if not findings:
        lines.extend(["確定した指摘はありません。", ""])
    else:
        lines.extend(
            [
                "| ID | 重要度 | 観点 | ファイル | 行 | 指摘 |",
                "|---|---|---|---|---:|---|",
            ]
        )
        for index, finding in enumerate(findings, start=1):
            lines.append(
                "| {id} | {severity} | {category} | {file} | {line} | {message} |".format(
                    id=_finding_id(index),
                    severity=_table(_severity(finding.severity)),
                    category=_table(_category(finding.category)),
                    file=_table(_code(finding.file) if finding.file else "—"),
                    line=_table(_finding_range(finding)),
                    message=_table(finding.message),
                )
            )
        lines.append("")

        lines.extend(["## 指摘詳細", ""])
        for index, finding in enumerate(findings, start=1):
            finding_id = _finding_id(index)
            lines.extend(
                [
                    f"### {finding_id} — {_heading(finding.message)}",
                    "",
                    f"**重要度:** {_severity(finding.severity)}  ",
                    f"**観点:** {_category(finding.category)}  ",
                    f"**対象:** {_location(finding)}",
                    "",
                    "#### 指摘",
                    "",
                    finding.message.strip(),
                    "",
                    "#### 根拠",
                    "",
                    (finding.rationale or "根拠の詳細はReviewerから提供されていません。").strip(),
                    "",
                    "#### 推奨対応",
                    "",
                    (finding.recommendation or "具体的な推奨対応はReviewerから提供されていません。").strip(),
                    "",
                    "#### AI確信度",
                    "",
                    _confidence(finding.confidence),
                    "",
                ]
            )
            if finding.reported_by:
                lines.extend(
                    [
                        "#### 報告Reviewer",
                        "",
                        *[f"- {item}" for item in finding.reported_by],
                        "",
                    ]
                )
            if finding.reported_severities:
                labels = ", ".join(_severity(item) for item in finding.reported_severities)
                lines.extend(["#### 報告された重要度", "", labels, ""])

    if final_result and final_result.human_checks:
        lines.extend(
            [
                "## 人間による確認事項",
                "",
                "| ID | 対象 | 確認事項 | AIだけで確定できない理由 |",
                "|---|---|---|---|",
            ]
        )
        for index, item in enumerate(final_result.human_checks, start=1):
            lines.append(
                f"| H-{index:03d} | {_table(_human_target(item))} | "
                f"{_table(_human_check_text(item))} | {_table(_human_reason(item))} |"
            )
        lines.append("")

    if final_result and final_result.excluded_findings:
        lines.extend(
            [
                "## 反証により除外した指摘",
                "",
                "| ID | 元Reviewer | 除外理由 |",
                "|---|---|---|",
            ]
        )
        for index, item in enumerate(final_result.excluded_findings, start=1):
            lines.append(
                f"| X-{index:03d} | {_table(_first(item, 'reported_by', 'reviewer', 'agent', default='—'))} | "
                f"{_table(_first(item, 'reason', 'rationale', 'description', 'message', default=_compact(item)))} |"
            )
        lines.append("")

    if final_result and final_result.conflicts:
        lines.extend(
            [
                "## Reviewer間の意見相違",
                "",
                "| ID | 関係Reviewer | 内容 |",
                "|---|---|---|",
            ]
        )
        for index, item in enumerate(final_result.conflicts, start=1):
            lines.append(
                f"| C-{index:03d} | {_table(_first(item, 'reviewers', 'reported_by', default='—'))} | "
                f"{_table(_first(item, 'description', 'message', 'reason', 'rationale', default=_compact(item)))} |"
            )
        lines.append("")

    if states:
        lines.extend(
            [
                "## レビュー担当の実行状況",
                "",
                "| Reviewer | 状態 |",
                "|---|---|",
            ]
        )
        for reviewer, state in states.items():
            lines.append(f"| {_table(reviewer)} | {_table(STATUS_LABELS.get(state, state))} |")
        lines.append("")

    if final_result and final_result.review_coverage:
        lines.extend(
            [
                "## レビュー実施範囲",
                "",
                "| 区分 | 内容 |",
                "|---|---|",
            ]
        )
        for key, value in final_result.review_coverage.items():
            lines.append(f"| {_table(_coverage_label(str(key)))} | {_table(_human_value(value))} |")
        lines.append("")

    lines.extend(
        [
            "## 実行メタデータ",
            "",
            f"- project_id: {project_id}",
        ]
    )
    if run_id:
        lines.append(f"- run_id: {run_id}")
    if execution_mode:
        lines.append(f"- execution_mode: {execution_mode}")
    if execution_strategy:
        lines.append(f"- execution_strategy: {execution_strategy}")
    lines.append("")

    return "\n".join(lines)


def _finding_id(index: int) -> str:
    return f"F-{index:03d}"


def _severity(value: str | None) -> str:
    if not value:
        return "不明"
    return SEVERITY_LABELS.get(value, value)


def _category(value: str | None) -> str:
    if not value:
        return "その他"
    return CATEGORY_LABELS.get(value, value)


def _decision(value: str) -> str:
    label = DECISION_LABELS.get(value, value)
    return f"{label} ({value})" if label != value else value


def _confidence(value: str | None) -> str:
    if not value:
        return "未指定"
    return CONFIDENCE_LABELS.get(value.lower(), value)


def _finding_range(finding: Finding) -> str:
    if finding.line_range:
        return finding.line_range
    if finding.range:
        return finding.range
    if finding.line is not None:
        return str(finding.line)
    return "—"


def _location(finding: Finding) -> str:
    if not finding.file:
        return "場所を特定できません"
    line = _finding_range(finding)
    suffix = "" if line == "—" else f":{line}"
    return _code(f"{finding.file}{suffix}")


def _heading(message: str) -> str:
    first = " ".join(message.strip().splitlines()).strip()
    if len(first) <= 80:
        return first
    return first[:77] + "..."


def _code(value: str) -> str:
    return f"`{value.replace('`', '')}`"


def _table(value: Any) -> str:
    text = _human_value(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _human_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    if isinstance(value, (list, tuple, set)):
        return "<br>".join(_human_value(item) for item in value) or "—"
    if isinstance(value, dict):
        return _compact(value)
    return str(value)


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _first(item: dict[str, object], *keys: str, default: str) -> str:
    for key in keys:
        if key in item and item[key] not in (None, "", [], {}):
            return _human_value(item[key])
    return default


def _human_target(item: dict[str, object]) -> str:
    file_value = _first(item, "file", "path", "target", default="—")
    line_value = _first(item, "line_range", "range", "line", default="")
    if line_value and line_value != "—":
        return f"{file_value}:{line_value}"
    return file_value


def _human_check_text(item: dict[str, object]) -> str:
    return _first(item, "check", "question", "description", "message", "summary", default=_compact(item))


def _human_reason(item: dict[str, object]) -> str:
    return _first(item, "reason", "rationale", "why", "missing_context", default="AIだけでは確定できません。")


def _coverage_label(value: str) -> str:
    return {
        "reviewed": "確認済み",
        "not_reviewed": "未確認",
        "missing_context": "不足情報",
    }.get(value, value)
