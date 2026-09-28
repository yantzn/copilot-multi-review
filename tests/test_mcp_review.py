from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from ai_review.mcp_review import (
    ReviewMcpError,
    clear_prepared_contexts,
    finalize_review_context,
    prepare_review_context,
)


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.decode("utf-8").strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("# Sample\n", encoding="utf-8")
    (path / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial")
    return path


@pytest.fixture(autouse=True)
def reset_contexts() -> None:
    clear_prepared_contexts()
    yield
    clear_prepared_contexts()


def test_prepare_review_returns_safe_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(tmp_path / "copilot-home"))
    repo = init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    result = prepare_review_context(
        str(repo),
        target="uncommitted",
        run_quality=False,
    )

    assert result["status"] == "ready"
    assert isinstance(result["review_context_id"], str)
    assert result["secret_scan_status"] == "passed"
    assert result["quality_check_status"] == "skipped"
    assert "diff --git" in result["diff"]
    assert result["repository"]["root"] == str(repo.resolve())
    assert result["requirements_context"]


def test_prepare_review_blocks_confirmed_secret_without_returning_diff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(tmp_path / "copilot-home"))
    repo = init_repo(tmp_path / "repo")
    (repo / "config.py").write_text(
        'TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz123456"\n',
        encoding="utf-8",
    )

    result = prepare_review_context(
        str(repo),
        target="uncommitted",
        run_quality=False,
    )

    assert result["status"] == "blocked"
    assert result["secret_scan_status"] == "blocked"
    assert result["review_context_id"] is None
    assert "diff" not in result
    assert result["secret_findings"]


def test_finalize_review_uses_stricter_deterministic_decision_and_writes_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "copilot-home"
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(home))
    repo = init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    prepared = prepare_review_context(
        str(repo),
        target="uncommitted",
        run_quality=False,
    )
    context_id = prepared["review_context_id"]
    assert isinstance(context_id, str)

    result = finalize_review_context(
        context_id,
        {
            "agent": "final",
            "status": "completed",
            "decision": "APPROVE",
            "findings": [
                {
                    "severity": "Major",
                    "category": "correctness",
                    "file": "app.py",
                    "line": 2,
                    "message": "戻り値の変更が呼び出し側の期待と一致しない可能性があります。",
                    "rationale": "変更前後で返却値が1から2へ変わっています。",
                    "recommendation": "呼び出し側の期待値と要件を確認してください。",
                    "confidence": "high",
                    "reported_by": ["Correctness Reviewer"],
                    "reported_severities": ["Major"],
                    "severity_conflict": False,
                }
            ],
            "summary": "重大な指摘候補が1件あります。",
            "reviewer_states": {
                "correctness": "completed",
                "final": "completed",
            },
            "human_checks": [],
            "conflicts": [],
            "excluded_findings": [],
            "review_coverage": {
                "reviewed": ["correctness"],
                "not_reviewed": [],
                "missing_context": [],
            },
        },
    )

    assert result["decision"] == "CHANGES_REQUIRED"
    assert result["ai_decision"] == "APPROVE"
    assert result["deterministic_decision"] == "CHANGES_REQUIRED"

    report = Path(str(result["report_path"]))
    assert report.exists()
    assert home in report.parents
    assert repo not in report.parents

    text = report.read_text(encoding="utf-8")
    assert "# コードレビュー結果" in text
    assert "| F-001 | 重大 | 正当性 | `app.py` | 2 |" in text
    assert "戻り値の変更が呼び出し側の期待と一致しない可能性があります。" in text
    assert "呼び出し側の期待値と要件を確認してください。" in text

    with pytest.raises(ReviewMcpError, match="prepare_review"):
        finalize_review_context(context_id, {"agent": "final"})


def test_finalize_review_without_reviewer_states_fails_safe_to_inconclusive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(tmp_path / "copilot-home"))
    repo = init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    prepared = prepare_review_context(
        str(repo),
        target="uncommitted",
        run_quality=False,
    )
    context_id = prepared["review_context_id"]
    assert isinstance(context_id, str)

    result = finalize_review_context(
        context_id,
        {
            "agent": "final",
            "status": "completed",
            "decision": "APPROVE",
            "findings": [],
            "summary": "指摘はありません。",
        },
    )

    assert result["decision"] == "INCONCLUSIVE"
    assert result["incomplete_review"] is True



def test_finalize_review_respects_final_reviewer_incomplete_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(tmp_path / "copilot-home"))
    repo = init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    prepared = prepare_review_context(
        str(repo),
        target="uncommitted",
        run_quality=False,
    )
    context_id = prepared["review_context_id"]
    assert isinstance(context_id, str)

    result = finalize_review_context(
        context_id,
        {
            "agent": "final",
            "status": "completed",
            "decision": "APPROVE",
            "findings": [],
            "summary": "実行済みReviewerに指摘はありません。",
            "reviewer_states": {
                "correctness": "completed",
                "final": "completed",
            },
            "incomplete_review": True,
        },
    )

    assert result["decision"] == "INCONCLUSIVE"
    assert result["incomplete_review"] is True



def test_finalize_review_honors_platform_output_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(tmp_path / "copilot-home"))
    output_home = tmp_path / "antigravity-output"
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_OUTPUT_HOME", str(output_home))
    repo = init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    prepared = prepare_review_context(str(repo), target="uncommitted", run_quality=False)
    context_id = prepared["review_context_id"]
    assert isinstance(context_id, str)

    result = finalize_review_context(
        context_id,
        {
            "agent": "final",
            "status": "completed",
            "decision": "APPROVE",
            "findings": [],
            "summary": "指摘はありません。",
            "reviewer_states": {
                "correctness": "completed",
                "final": "completed",
            },
        },
    )

    report = Path(str(result["report_path"]))
    assert output_home in report.parents
    assert repo not in report.parents


def test_finalize_review_rejects_invalid_severity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(tmp_path / "copilot-home"))
    repo = init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    prepared = prepare_review_context(str(repo), target="uncommitted", run_quality=False)
    context_id = prepared["review_context_id"]
    assert isinstance(context_id, str)

    with pytest.raises(ReviewMcpError, match="severity"):
        finalize_review_context(
            context_id,
            {
                "agent": "final",
                "status": "completed",
                "decision": "APPROVE",
                "findings": [{"severity": "High", "message": "invalid"}],
                "summary": "invalid",
                "reviewer_states": {"correctness": "completed"},
            },
        )
