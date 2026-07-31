from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess

import pytest

from ai_review.repository import resolve_repository
from ai_review.repository_audit import (
    AuditOptions,
    PROFILE_AGENTS,
    analyze_repository,
    run_repository_audit,
    split_batches,
    validate_profile,
    AuditError,
)
from ai_review.review_engine import CopilotClient
from ai_review.storage import RootPaths, save_repository_audit_result


class CountingClient(CopilotClient):
    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0
        self.calls: list[str] = []

    def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
        agent = prompt.split("Repository audit agent: ", 1)[1].splitlines()[0]
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        self.calls.append(agent)
        self.active_calls -= 1
        return json.dumps(
            {
                "run_id": "run-1",
                "agent": agent,
                "provider": "github-copilot-cli",
                "schema_version": "0.1.0",
                "decision": "APPROVE",
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
    (path / "src").mkdir()
    (path / "tests").mkdir()
    (path / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (path / "tests" / "test_app.py").write_text("from src.app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    (path / "README.md").write_text("# sample\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial")
    return path


def test_analyze_repository_collects_git_files_and_excludes(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (repo / "image.png").write_bytes(b"\x89PNG")
    git(repo, "add", ".env", "image.png")
    git(repo, "commit", "-m", "excluded")

    analysis, batches = analyze_repository(resolve_repository(str(repo)), AuditOptions())

    assert any(item.path == "src/app.py" for item in analysis.target_files)
    assert any(item.path == ".env" and item.reason == "secret_file" for item in analysis.coverage)
    assert any(item.path == "image.png" and item.reason == "binary_extension" for item in analysis.coverage)
    assert batches
    assert analysis.expected_copilot_calls == sum(len(batch.agents) for batch in batches) + len(PROFILE_AGENTS["standard"]["cross"])


def test_include_untracked_adds_safe_text(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "未追跡 file.py").write_text("print('x')\n", encoding="utf-8")

    analysis, _ = analyze_repository(resolve_repository(str(repo)), AuditOptions(include_untracked=True))

    assert any(item.path == "未追跡 file.py" for item in analysis.target_files)


def test_batch_split_is_stable_and_respects_limits(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    context = resolve_repository(str(repo))
    analysis, _ = analyze_repository(context, AuditOptions(max_files_per_batch=1, max_lines_per_batch=10))

    first = split_batches(analysis.target_files, AuditOptions(max_files_per_batch=1, max_lines_per_batch=10))
    second = split_batches(analysis.target_files, AuditOptions(max_files_per_batch=1, max_lines_per_batch=10))

    assert [batch.batch_id for batch in first] == [batch.batch_id for batch in second]
    assert all(len(batch.files) <= 1 for batch in first)


def test_profiles_and_invalid_profile() -> None:
    assert PROFILE_AGENTS["quick"]["batch"] == ["correctness", "security"]
    assert PROFILE_AGENTS["standard"]["cross"][-1] == "final"
    assert "devil_advocate" in PROFILE_AGENTS["deep"]["batch"]
    with pytest.raises(AuditError):
        validate_profile("wild")


def test_repository_audit_runs_serially_with_mock(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    context = resolve_repository(str(repo))
    client = CountingClient()

    _analysis, result = run_repository_audit(
        context,
        AuditOptions(profile="quick", max_files_per_batch=1),
        run_id="run-1",
        client=client,
    )

    assert result.max_active_calls == 1
    assert client.max_active_calls == 1
    assert result.copilot_call_count == len(client.calls)


def test_secret_file_blocks_copilot_zero_calls(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "src" / "secret.py").write_text("TOKEN='ghp_123456789012345678901234567890123456'\n", encoding="utf-8")
    git(repo, "add", "src/secret.py")
    git(repo, "commit", "-m", "secret")
    context = resolve_repository(str(repo))
    client = CountingClient()

    _analysis, result = run_repository_audit(context, AuditOptions(profile="quick"), run_id="run-1", client=client)

    assert result.final_decision == "BLOCKED"
    assert result.copilot_call_count == 0
    assert client.calls == []


def test_audit_report_outputs_to_engine_only(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    context = resolve_repository(str(repo))
    analysis, result = run_repository_audit(context, AuditOptions(no_agents=True), run_id="run-1", client=CountingClient())
    paths = RootPaths.from_engine_root(tmp_path / "engine")

    save_repository_audit_result(paths, context, analysis, result, request={"profile": "standard"})

    assert (paths.output_root / context.project_id / "latest" / "coverage.json").exists()
    assert (paths.output_root / context.project_id / "history" / "run-1" / "repository-summary.json").exists()
    assert not (repo / "reports").exists()
    assert not (repo / "runtime").exists()


def test_worktree_supported(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    worktree = tmp_path / "work tree"
    git(repo, "worktree", "add", str(worktree), "-b", "feature")

    analysis, _ = analyze_repository(resolve_repository(str(worktree)), AuditOptions())

    assert any(item.path == "src/app.py" for item in analysis.target_files)


def test_symlink_outside_excluded_when_supported(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windowsのsymlink権限に依存するためPOSIXで確認します。")
    repo = init_repo(tmp_path / "repo")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (repo / "outside-link.txt").symlink_to(outside)
    git(repo, "add", "outside-link.txt")
    git(repo, "commit", "-m", "link")

    analysis, _ = analyze_repository(resolve_repository(str(repo)), AuditOptions())

    assert any(item.path == "outside-link.txt" and item.reason == "symlink_outside_repository" for item in analysis.coverage)
