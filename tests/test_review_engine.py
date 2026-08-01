from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import time

import pytest

from ai_review.diff_collector import collect_diff
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
from ai_review.copilot import CopilotCommand


class FakeClient(CopilotClient):
    def __init__(self, decisions: list[str] | None = None) -> None:
        self.calls: list[str] = []
        self.decisions = decisions or []

    def run_prompt(self, prompt: str, *, timeout_seconds: int, cancel_requested=None) -> str:
        payload = json.loads(prompt.split("PAYLOAD_JSON\n", 1)[1].splitlines()[0])
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
    progress: list[dict] = []
    request = request_for(repo)
    request = EngineRequest(
        repository=request.repository,
        diff=request.diff,
        quality_checks=request.quality_checks,
        target=request.target,
        run_id=request.run_id,
        progress_callback=progress.append,
    )

    result = run_review_engine(request, client)

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
    assert progress[0]["current_agent"] == "requirements"
    assert progress[0]["current_agent_index"] == 1
    assert progress[0]["total_agents"] == 9


def test_single_agent_run_skips_others(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    result = run_review_engine(request_for(repo, agent="security"), FakeClient())

    assert result.agent_states["security"] == "completed"
    assert result.agent_states["requirements"] == "skipped"


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(AgentSchemaError):
        parse_agent_response("not json", run_id="r", agent="security")


def test_schema_mismatch_is_rejected() -> None:
    with pytest.raises(AgentSchemaError):
        parse_agent_response("{}", run_id="r", agent="security")


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
        def run_prompt(self, prompt: str, *, timeout_seconds: int, cancel_requested=None) -> str:
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


def test_schema_mismatch_retries_safely(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    class RetryClient(FakeClient):
        def run_prompt(self, prompt: str, *, timeout_seconds: int, cancel_requested=None) -> str:
            if not self.calls:
                self.calls.append("bad")
                return "{}"
            return super().run_prompt(prompt, timeout_seconds=timeout_seconds)

    client = RetryClient()
    result = run_review_engine(request_for(repo, agent="security"), client)

    assert result.agent_states["security"] == "completed"
    assert client.calls == ["bad", "security"]


def test_timeout_marks_agent_failed(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    class TimeoutClient(FakeClient):
        def run_prompt(self, prompt: str, *, timeout_seconds: int, cancel_requested=None) -> str:
            raise subprocess.TimeoutExpired(cmd="copilot", timeout=1)

    result = run_review_engine(request_for(repo, agent="security"), TimeoutClient())

    assert result.agent_states["security"] == "failed"
    assert result.final_decision == "INCONCLUSIVE"


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
        def run_prompt(self, prompt: str, *, timeout_seconds: int, cancel_requested=None) -> str:
            raise ReviewEngineError("Copilot CLI rate limit")

    result = run_review_engine(request_for(repo, agent="security"), FailingClient())

    assert result.agent_states["security"] == "failed"
    assert result.final_decision == "INCONCLUSIVE"


def test_copilot_client_normal_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai_review.review_engine.resolve_copilot_command",
        lambda _args: CopilotCommand(sys.executable, [sys.executable, "-c", "import sys; sys.stdin.read(); print('ok')"]),
    )

    assert CopilotClient().run_prompt("hello", timeout_seconds=5).strip() == "ok"


def test_copilot_client_cancels_running_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ai_review.review_engine.resolve_copilot_command",
        lambda _args: CopilotCommand(sys.executable, [sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(10)"]),
    )
    started = time.monotonic()

    with pytest.raises(AgentCancelledError):
        CopilotClient().run_prompt("hello", timeout_seconds=30, cancel_requested=lambda: time.monotonic() - started > 0.5)

    assert time.monotonic() - started < 10
