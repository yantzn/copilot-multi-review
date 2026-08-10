from __future__ import annotations

from pathlib import Path
import json
import subprocess

import pytest

from ai_review.diff_collector import collect_diff
from ai_review.agents import AGENT_ORDER, stricter_decision
from ai_review.quality import QualityCheckResult
from ai_review.repository import resolve_repository
from ai_review.review_engine import (
    AgentCancelledError,
    AgentRunIdMismatchError,
    AgentSchemaError,
    CopilotClient,
    EngineRequest,
    ReviewEngineError,
    parse_agent_response,
    run_review_engine,
)


class FakeClient(CopilotClient):
    def __init__(self, decisions: list[str] | None = None) -> None:
        self.calls: list[str] = []
        self.payloads: list[dict] = []
        self.decisions = decisions or []

    def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
        payload = json.loads(prompt.split("PAYLOAD_JSON\n", 1)[1].splitlines()[0])
        self.payloads.append(payload)
        self.calls.append(payload["agent"])
        decision = self.decisions.pop(0) if self.decisions else "APPROVE"
        return json.dumps(
            {
                "run_id": payload["run_id"],
                "agent": payload["agent"],
                "provider": "github-copilot-cli",
                "schema_version": "0.1.0",
                "decision": decision,
                "findings": [],
                "summary": "ok",
            }
        )


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.decode("utf-8").strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("# sample\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")
    return path


def request_for(repo: Path, *, agent: str | None = None, run_id: str = "run-1") -> EngineRequest:
    repository = resolve_repository(str(repo))
    diff = collect_diff(repository, target="base")
    return EngineRequest(
        repository=repository,
        diff=diff,
        quality_checks=[QualityCheckResult(name="quality", command=[], status="skipped")],
        target="base",
        run_id=run_id,
        agent=agent,
    )


def test_runs_nine_agents_serially(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    client = FakeClient()

    result = run_review_engine(request_for(repo), client)

    assert client.calls == [
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
    assert result.max_concurrent_copilot_processes == 1
    assert all(state == "completed" for state in result.agent_states.values())


def test_single_agent_run_skips_others(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    client = FakeClient()
    result = run_review_engine(request_for(repo, agent="security"), client)

    assert result.agent_states["security"] == "completed"
    assert result.agent_states["final"] == "completed"
    assert result.agent_states["requirements"] == "skipped"
    assert client.calls == ["security", "final"]


def test_single_specialist_run_still_sends_result_to_final(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    client = FakeClient()

    run_review_engine(request_for(repo, agent="security"), client)

    final_payload = client.payloads[-1]
    assert final_payload["agent"] == "final"
    assert [item["agent"] for item in final_payload["specialist_results"]] == ["security"]
    assert final_payload["reviewer_states"] == {"security": "completed"}


def test_final_agent_receives_all_specialist_results(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    client = FakeClient()

    run_review_engine(request_for(repo), client)

    final_payload = client.payloads[-1]
    assert final_payload["agent"] == "final"
    assert "specialist_results" in final_payload
    assert [item["agent"] for item in final_payload["specialist_results"]] == AGENT_ORDER[:-1]
    assert set(final_payload["reviewer_states"]) == set(AGENT_ORDER[:-1])
    assert final_payload["truncation_status"] == "complete"
    assert final_payload["quality_check_status"] == [{"name": "quality", "status": "skipped", "returncode": None}]
    assert "previous_results" not in final_payload
    assert "diff" not in final_payload


def test_non_final_agents_do_not_receive_previous_results(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    client = FakeClient()

    run_review_engine(request_for(repo), client)

    final_payload = client.payloads[-1]
    devil_payload = client.payloads[-2]
    assert "previous_results" not in devil_payload
    assert "specialist_results" not in devil_payload
    assert len(final_payload["specialist_results"]) == 8


def test_python_final_prompt_documents_integration_contract() -> None:
    text = Path("agents/final.md").read_text(encoding="utf-8")

    for term in [
        "specialist_results",
        "reviewer_states",
        "reported_by",
        "reported_severities",
        "severity_conflict",
        "conflicts",
        "incomplete_review",
        "Critical > Major > Minor > Info",
    ]:
        assert term in text
    assert "not only the last two reviewers" in text


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(AgentSchemaError):
        parse_agent_response("not json", run_id="r", agent="security")


def test_schema_mismatch_is_rejected() -> None:
    with pytest.raises(AgentSchemaError):
        parse_agent_response("{}", run_id="r", agent="security")


def test_parse_agent_response_accepts_final_reviewer_finding_metadata() -> None:
    raw = json.dumps(
        {
            "run_id": "r",
            "agent": "final",
            "provider": "github-copilot-cli",
            "schema_version": "0.1.0",
            "decision": "CHANGES_REQUIRED",
            "findings": [
                {
                    "severity": "Major",
                    "category": "security",
                    "file": "foo.py",
                    "line/range": "10-12",
                    "message": "unsafe subprocess",
                    "rationale": "Security Reviewer reported command injection risk.",
                    "recommendation": "Avoid shell execution.",
                    "confidence": "high",
                    "reported_by": ["Security Reviewer", "Correctness Reviewer"],
                    "reported_severities": ["Major", "Major"],
                    "severity_conflict": False,
                    "extra_future_field": "ignored",
                }
            ],
            "summary": "deduplicated",
            "status": "inconclusive",
            "reviewer_states": {"security": "failed"},
            "conflicts": [{"reviewers": ["Requirements Reviewer", "Correctness Reviewer"]}],
            "incomplete_review": True,
        }
    )

    result = parse_agent_response(raw, run_id="r", agent="final")

    finding = result.findings[0]
    assert finding.line_range == "10-12"
    assert finding.reported_by == ["Security Reviewer", "Correctness Reviewer"]
    assert finding.reported_severities == ["Major", "Major"]
    assert finding.severity_conflict is False
    assert result.status == "inconclusive"
    assert result.reviewer_states == {"security": "failed"}
    assert result.conflicts == [{"reviewers": ["Requirements Reviewer", "Correctness Reviewer"]}]
    assert result.incomplete_review is True


def test_run_id_mismatch_is_rejected() -> None:
    raw = json.dumps(
        {
            "run_id": "other",
            "agent": "security",
            "provider": "github-copilot-cli",
            "schema_version": "0.1.0",
            "decision": "APPROVE",
            "findings": [],
            "summary": "ok",
        }
    )
    with pytest.raises(AgentRunIdMismatchError):
        parse_agent_response(raw, run_id="r", agent="security")


def test_major_finding_forces_changes_required(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    class MajorClient(FakeClient):
        def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
            payload = json.loads(prompt.split("PAYLOAD_JSON\n", 1)[1].splitlines()[0])
            self.calls.append(payload["agent"])
            return json.dumps(
                {
                    "run_id": payload["run_id"],
                    "agent": payload["agent"],
                    "provider": "github-copilot-cli",
                    "schema_version": "0.1.0",
                    "decision": "APPROVE",
                    "findings": [{"severity": "Major", "message": "bug"}],
                    "summary": "bug",
                }
            )

    result = run_review_engine(request_for(repo), MajorClient())

    assert result.final_decision == "CHANGES_REQUIRED"


def test_failed_quality_check_forces_inconclusive_even_when_ai_approves(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    request = request_for(repo)
    request = EngineRequest(
        repository=request.repository,
        diff=request.diff,
        quality_checks=[
            QualityCheckResult(name="pytest", command=["python", "-m", "pytest"], status="failed", returncode=1)
        ],
        target=request.target,
        run_id=request.run_id,
    )

    result = run_review_engine(request, FakeClient())

    assert result.final_decision == "INCONCLUSIVE"


def test_schema_mismatch_retries_safely(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    class RetryClient(FakeClient):
        def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
            if not self.calls:
                self.calls.append("bad")
                return "{}"
            return super().run_prompt(prompt, timeout_seconds=timeout_seconds)

    client = RetryClient()
    result = run_review_engine(request_for(repo, agent="security"), client)

    assert result.agent_states["security"] == "completed"
    assert client.calls == ["bad", "security", "final"]


def test_timeout_marks_agent_failed(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    class TimeoutClient(FakeClient):
        def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
            raise subprocess.TimeoutExpired(cmd="copilot", timeout=1)

    result = run_review_engine(request_for(repo, agent="security"), TimeoutClient())

    assert result.agent_states["security"] == "failed"
    assert result.final_decision == "INCONCLUSIVE"


def test_failed_specialist_does_not_approve_even_if_final_approves(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    class OneFailedClient(FakeClient):
        def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
            payload = json.loads(prompt.split("PAYLOAD_JSON\n", 1)[1].splitlines()[0])
            if payload["agent"] == "security":
                raise ReviewEngineError("subagent failed")
            self.calls.append(payload["agent"])
            return json.dumps(
                {
                    "run_id": payload["run_id"],
                    "agent": payload["agent"],
                    "provider": "github-copilot-cli",
                    "schema_version": "0.1.0",
                    "decision": "APPROVE",
                    "findings": [],
                    "summary": "ok",
                }
            )

    result = run_review_engine(request_for(repo, agent="security"), OneFailedClient())

    assert result.agent_states["security"] == "failed"
    assert result.agent_states["final"] == "completed"
    assert result.final_decision == "INCONCLUSIVE"


def test_final_failure_does_not_approve(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    class FinalFailedClient(FakeClient):
        def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
            payload = json.loads(prompt.split("PAYLOAD_JSON\n", 1)[1].splitlines()[0])
            if payload["agent"] == "final":
                return "{"
            return super().run_prompt(prompt, timeout_seconds=timeout_seconds)

    result = run_review_engine(request_for(repo, agent="security"), FinalFailedClient())

    assert result.agent_states["final"] == "failed"
    assert result.final_decision == "INCONCLUSIVE"


def test_limited_parallel_keeps_specialists_independent(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    request = request_for(repo)
    request = EngineRequest(
        repository=request.repository,
        diff=request.diff,
        quality_checks=request.quality_checks,
        target=request.target,
        run_id=request.run_id,
        orchestration_strategy="limited_parallel",
        max_parallel_reviewers=2,
    )
    client = FakeClient()

    result = run_review_engine(request, client)

    assert result.execution_strategy == "limited_parallel"
    assert result.max_concurrent_copilot_processes == 2
    assert all("previous_results" not in payload for payload in client.payloads if payload["agent"] != "final")
    assert client.payloads[-1]["agent"] == "final"


def test_timing_fields_are_recorded(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    result = run_review_engine(request_for(repo, agent="security"), FakeClient())

    assert result.started_at
    assert result.finished_at
    assert result.duration_ms is not None
    assert result.orchestrator_duration_ms is not None
    assert result.final_reviewer_duration_ms is not None
    assert set(result.agent_durations_ms or {}) == {"security", "final"}


def test_invalid_strategy_is_rejected(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    request = request_for(repo)
    request = EngineRequest(
        repository=request.repository,
        diff=request.diff,
        quality_checks=request.quality_checks,
        target=request.target,
        run_id=request.run_id,
        orchestration_strategy="bad",  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError):
        run_review_engine(request, FakeClient())


def test_windows_japanese_path_review_report_fields(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "レビュー対象" / "サンプル")

    result = run_review_engine(request_for(repo, agent="security"), FakeClient())

    assert result.final_decision == "APPROVE"
    assert result.agent_states["security"] == "completed"
    assert result.agent_durations_ms


def test_cancel_between_agents(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    cancel_file = tmp_path / "cancel.json"
    cancel_file.write_text("{}", encoding="utf-8")
    request = request_for(repo)
    request = EngineRequest(
        repository=request.repository,
        diff=request.diff,
        quality_checks=request.quality_checks,
        target=request.target,
        run_id=request.run_id,
        cancel_file=cancel_file,
    )

    with pytest.raises(AgentCancelledError):
        run_review_engine(request, FakeClient())


def test_rate_limit_or_auth_failure_marks_failed(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    class FailingClient(FakeClient):
        def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
            raise ReviewEngineError("Copilot CLI rate limit")

    result = run_review_engine(request_for(repo, agent="security"), FailingClient())

    assert result.agent_states["security"] == "failed"
    assert result.final_decision == "INCONCLUSIVE"


@pytest.mark.parametrize(
    ("ai_decision", "rule_decision", "expected"),
    [
        ("APPROVE", "CHANGES_REQUIRED", "CHANGES_REQUIRED"),
        ("APPROVE_WITH_NOTES", "BLOCKED", "BLOCKED"),
        ("CHANGES_REQUIRED", "APPROVE", "CHANGES_REQUIRED"),
        ("APPROVE", "INCONCLUSIVE", "INCONCLUSIVE"),
    ],
)
def test_stricter_decision_keeps_safer_outcome(ai_decision: str, rule_decision: str, expected: str) -> None:
    assert stricter_decision(rule_decision, ai_decision) == expected
