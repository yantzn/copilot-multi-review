from __future__ import annotations

from ai_review.agents import AgentResult, Finding
from ai_review.review_markdown import render_review_markdown


def test_render_review_markdown_uses_japanese_labels_and_specific_location() -> None:
    result = AgentResult(
        run_id="run-1",
        agent="final",
        provider="github-copilot-cli",
        schema_version="0.1.0",
        decision="CHANGES_REQUIRED",
        findings=[
            Finding(
                severity="Major",
                category="correctness",
                file="src/service/user.py",
                line=84,
                message="例外発生時にトランザクションがロールバックされない可能性があります。",
                rationale="DB更新後の例外経路にロールバック処理が確認できません。",
                recommendation="トランザクション境界と例外時のロールバックを確認してください。",
                confidence="high",
                reported_by=["Correctness Reviewer", "Operations Reviewer"],
                reported_severities=["Major", "Minor"],
            )
        ],
        summary="重大な指摘が1件あります。",
        reviewer_states={"correctness": "completed", "security": "completed"},
        human_checks=[
            {
                "file": "src/auth/policy.py",
                "line": 40,
                "question": "管理者だけに許可する仕様か確認してください。",
                "reason": "要件資料に権限条件の明記がありません。",
            }
        ],
        excluded_findings=[
            {
                "reviewer": "Performance Reviewer",
                "reason": "対象処理がバッチ限定であることを設計資料から確認できたため。",
            }
        ],
        review_coverage={
            "reviewed": ["correctness", "security"],
            "not_reviewed": [],
            "missing_context": ["権限仕様"],
        },
    )

    markdown = render_review_markdown(
        project_id="github.com__example__repo",
        target="mainとの差分",
        final_decision="CHANGES_REQUIRED",
        final_result=result,
        changed_file_count=3,
        diff_line_count=120,
        truncated=False,
        reviewer_states=result.reviewer_states,
        run_id="run-1",
        execution_mode="subagent",
        execution_strategy="native",
    )

    assert "| F-001 | 重大 | 正当性 | `src/service/user.py` | 84 |" in markdown
    assert "**重要度:** 重大" in markdown
    assert "**観点:** 正当性" in markdown
    assert "**対象:** `src/service/user.py:84`" in markdown
    assert "例外発生時にトランザクションがロールバックされない可能性があります。" in markdown
    assert "トランザクション境界と例外時のロールバックを確認してください。" in markdown
    assert "## 人間による確認事項" in markdown
    assert "## 反証により除外した指摘" in markdown
    assert "| 確認済み | correctness<br>security |" in markdown
    assert "重大, 軽微" in markdown


def test_render_review_markdown_keeps_machine_enums_outside_user_labels() -> None:
    result = AgentResult(
        run_id="run-2",
        agent="final",
        provider="github-copilot-cli",
        schema_version="0.1.0",
        decision="BLOCKED",
        findings=[
            Finding(
                severity="Critical",
                category="security",
                file="src/auth.py",
                line_range="10-12",
                message="認証を迂回できる可能性があります。",
            )
        ],
        summary="確認が必要です。",
    )

    markdown = render_review_markdown(
        project_id="example",
        target="PR #1",
        final_decision="BLOCKED",
        final_result=result,
        changed_file_count=1,
        diff_line_count=10,
        truncated=False,
    )

    assert "| 致命的 | 1 |" in markdown
    assert "| F-001 | 致命的 | セキュリティ | `src/auth.py` | 10-12 |" in markdown
    assert "ブロック (BLOCKED)" in markdown
