from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess

import pytest

from ai_review.repository import resolve_repository
from ai_review.repository_audit import (
    AuditError,
    AuditOptions,
    PROFILE_AGENTS,
    _classify_copilot_exception,
    analyze_repository,
    run_repository_audit,
    split_batches,
    validate_profile,
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


class FailingClient(CountingClient):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
        self.calls.append(prompt)
        raise self.exc


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


def test_analyze_repository_collects_git_files_and_blocks_secret_file(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (repo / "image.png").write_bytes(b"\x89PNG")
    git(repo, "add", ".env", "image.png")
    git(repo, "commit", "-m", "excluded")

    analysis, batches = analyze_repository(resolve_repository(str(repo)), AuditOptions())

    assert any(item.path == "src/app.py" for item in analysis.target_files)
    assert any(item.path == ".env" and item.status == "blocked" and item.reason == "confirmed_secret_file" for item in analysis.coverage)
    assert any(item.path == "image.png" and item.reason == "binary_extension" for item in analysis.coverage)
    assert batches
    assert analysis.expected_copilot_calls == sum(len(batch.agents) for batch in batches) + len(PROFILE_AGENTS["standard"]["cross"])


def test_include_untracked_adds_safe_text_and_marks_untracked(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "日本語 file.py").write_text("print('x')\n", encoding="utf-8")

    analysis, _ = analyze_repository(resolve_repository(str(repo)), AuditOptions(include_untracked=True))

    untracked = next(item for item in analysis.target_files if item.path == "日本語 file.py")
    assert untracked.tracked is False
    assert next(item for item in analysis.target_files if item.path == "src/app.py").tracked is True


def test_untracked_excluded_without_flag(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "new.py").write_text("print('new')\n", encoding="utf-8")

    analysis, _ = analyze_repository(resolve_repository(str(repo)), AuditOptions())

    assert all(item.path != "new.py" for item in analysis.target_files)


def test_batch_split_is_stable_and_respects_limits(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    context = resolve_repository(str(repo))
    analysis, _ = analyze_repository(context, AuditOptions(max_files_per_batch=1, max_lines_per_batch=10))

    first = split_batches(analysis.target_files, AuditOptions(max_files_per_batch=1, max_lines_per_batch=10))
    second = split_batches(analysis.target_files, AuditOptions(max_files_per_batch=1, max_lines_per_batch=10))

    assert [batch.batch_id for batch in first] == [batch.batch_id for batch in second]
    assert all(len({segment.path for segment in batch.files}) <= 1 for batch in first)


def test_large_file_is_split_into_line_range_segments(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    lines = [f"line {index}\n" for index in range(1, 15001)]
    (repo / "src" / "large.py").write_text("".join(lines), encoding="utf-8")
    git(repo, "add", "src/large.py")
    git(repo, "commit", "-m", "large")

    analysis, batches = analyze_repository(
        resolve_repository(str(repo)),
        AuditOptions(max_lines_per_batch=5000, max_chars_per_batch=100000, max_file_bytes=300000),
    )

    segments = [segment for batch in batches for segment in batch.files if segment.path == "src/large.py"]
    assert [(item.start_line, item.end_line) for item in segments] == [(1, 5000), (5001, 10000), (10001, 15000)]
    assert segments[0].content.splitlines() == [line.rstrip("\n") for line in lines[:5000]]
    assert segments[1].content.splitlines() == [line.rstrip("\n") for line in lines[5000:10000]]
    assert segments[2].content.splitlines() == [line.rstrip("\n") for line in lines[10000:]]
    assert "\n".join("".join(segment.content for segment in segments).splitlines()) == "\n".join(line.rstrip("\n") for line in lines)
    coverage = next(item for item in analysis.coverage if item.path == "src/large.py")
    assert len(coverage.segments) == 3


def test_segment_split_by_char_limit_and_single_long_line_skip(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "src" / "chars.py").write_text("aaaa\nbbbb\ncccc\n", encoding="utf-8")
    (repo / "src" / "long_line.py").write_text("x" * 20, encoding="utf-8")
    git(repo, "add", "src/chars.py", "src/long_line.py")
    git(repo, "commit", "-m", "chars")

    analysis, batches = analyze_repository(resolve_repository(str(repo)), AuditOptions(max_lines_per_batch=10, max_chars_per_batch=6))

    char_segments = [segment for batch in batches for segment in batch.files if segment.path == "src/chars.py"]
    assert [segment.content.splitlines() for segment in char_segments] == [["aaaa"], ["bbbb"], ["cccc"]]
    assert any(item.path == "src/long_line.py" and item.status == "skipped" and item.reason == "single_line_exceeds_char_limit" for item in analysis.coverage)


def test_empty_file_and_final_line_without_newline_segments(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "empty.txt").write_text("", encoding="utf-8")
    (repo / "no-newline.txt").write_text("one\ntwo", encoding="utf-8")
    git(repo, "add", "empty.txt", "no-newline.txt")
    git(repo, "commit", "-m", "edge lines")

    _analysis, batches = analyze_repository(resolve_repository(str(repo)), AuditOptions())

    empty = [segment for batch in batches for segment in batch.files if segment.path == "empty.txt"]
    no_newline = [segment for batch in batches for segment in batch.files if segment.path == "no-newline.txt"]
    assert [(item.start_line, item.end_line, item.content) for item in empty] == [(0, 0, "")]
    assert no_newline[0].end_line == 2
    assert no_newline[0].content.splitlines() == ["one", "two"]


def test_profiles_and_invalid_profile() -> None:
    assert PROFILE_AGENTS["quick"]["batch"] == ["correctness", "security"]
    assert PROFILE_AGENTS["standard"]["cross"][-1] == "final"
    assert PROFILE_AGENTS["deep"]["batch"] == ["requirements", "correctness", "security", "testing", "maintainability", "performance", "operations", "devil_advocate"]
    assert PROFILE_AGENTS["deep"]["cross"] == ["final"]
    with pytest.raises(AuditError):
        validate_profile("wild")


def test_repository_audit_runs_serially_with_mock(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    context = resolve_repository(str(repo))
    client = CountingClient()

    _analysis, result = run_repository_audit(context, AuditOptions(profile="quick", max_files_per_batch=1), run_id="run-1", client=client)

    assert result.max_active_calls == 1
    assert client.max_active_calls == 1
    assert result.copilot_call_count == len(client.calls)
    assert result.failed_batches == []


def test_secret_payload_blocks_copilot_zero_calls(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "src" / "secret.py").write_text("TOKEN='ghp_123456789012345678901234567890123456'\n", encoding="utf-8")
    git(repo, "add", "src/secret.py")
    git(repo, "commit", "-m", "secret")
    client = CountingClient()

    _analysis, result = run_repository_audit(resolve_repository(str(repo)), AuditOptions(profile="quick"), run_id="run-1", client=client)

    assert result.final_decision == "BLOCKED"
    assert result.copilot_call_count == 0
    assert client.calls == []


def test_secret_file_paths_block_without_sending_values(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    for name in (".env", ".env.local", "deploy.key", "cert.pem", "cert.p12"):
        (repo / name).write_text("SECRET_VALUE_SHOULD_NOT_APPEAR\n", encoding="utf-8")
        git(repo, "add", name)
    git(repo, "commit", "-m", "secret files")
    client = CountingClient()

    analysis, result = run_repository_audit(resolve_repository(str(repo)), AuditOptions(profile="quick"), run_id="run-1", client=client)

    assert result.final_decision == "BLOCKED"
    assert result.copilot_call_count == 0
    blocked = {item.path for item in analysis.coverage if item.status == "blocked"}
    assert {".env", ".env.local", "deploy.key", "cert.pem", "cert.p12"} <= blocked


def test_secret_fixture_file_is_non_blocking_sample(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    fixture = repo / "tests" / "fixtures"
    fixture.mkdir()
    (fixture / "sample.pem").write_text("-----BEGIN CERTIFICATE-----\nSAMPLE\n-----END CERTIFICATE-----\n", encoding="utf-8")
    git(repo, "add", "tests/fixtures/sample.pem")
    git(repo, "commit", "-m", "fixture pem")

    analysis, _ = analyze_repository(resolve_repository(str(repo)), AuditOptions())

    item = next(item for item in analysis.coverage if item.path == "tests/fixtures/sample.pem")
    assert item.status == "excluded"
    assert item.reason == "secret_sample_excluded"


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
    summary = json.loads((paths.output_root / context.project_id / "latest" / "repository-summary.json").read_text(encoding="utf-8"))
    assert summary["execution_mode"] == "analysis_only"
    assert summary["review_completed"] is False


def test_worktree_supported(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    worktree = tmp_path / "work tree"
    git(repo, "worktree", "add", str(worktree), "-b", "feature")

    analysis, _ = analyze_repository(resolve_repository(str(worktree)), AuditOptions())

    assert any(item.path == "src/app.py" for item in analysis.target_files)


def test_symlink_outside_excluded_when_supported(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows symlink privileges vary by environment.")
    repo = init_repo(tmp_path / "repo")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (repo / "outside-link.txt").symlink_to(outside)
    git(repo, "add", "outside-link.txt")
    git(repo, "commit", "-m", "link")

    analysis, _ = analyze_repository(resolve_repository(str(repo)), AuditOptions())

    assert any(item.path == "outside-link.txt" and item.reason == "symlink_outside_repository" for item in analysis.coverage)


def test_symlink_inside_and_broken_excluded_when_supported(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows symlink privileges vary by environment.")
    repo = init_repo(tmp_path / "repo")
    (repo / "inside-link.py").symlink_to(repo / "src" / "app.py")
    (repo / "broken-link.py").symlink_to(repo / "missing.py")
    git(repo, "add", "inside-link.py", "broken-link.py")
    git(repo, "commit", "-m", "links")

    analysis, _ = analyze_repository(resolve_repository(str(repo)), AuditOptions())

    assert any(item.path == "inside-link.py" and item.reason == "symlink" for item in analysis.coverage)
    assert any(item.path == "broken-link.py" and item.reason == "broken_or_loop_symlink" for item in analysis.coverage)


def test_cancelled_batches_are_not_failed(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    cancel_file = tmp_path / "cancel.json"
    cancel_file.write_text("{}", encoding="utf-8")

    _analysis, result = run_repository_audit(
        resolve_repository(str(repo)),
        AuditOptions(profile="quick", max_files_per_batch=1),
        run_id="run-1",
        client=CountingClient(),
        cancel_file=cancel_file,
    )

    assert result.final_decision == "INCONCLUSIVE"
    assert result.cancelled_batches
    assert result.failed_batches == []


def test_copilot_exception_is_classified(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")

    _analysis, result = run_repository_audit(
        resolve_repository(str(repo)),
        AuditOptions(profile="quick"),
        run_id="run-1",
        client=FailingClient(RuntimeError("rate limit exceeded with sensitive prompt hidden")),
    )

    assert result.failed_batches
    assert result.errors[0].kind == "rate_limit"
    assert result.errors[0].retryable is True


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("authentication failed", "authentication"),
        ("rate limit exceeded", "rate_limit"),
        ("request timeout", "timeout"),
        ("schema validation failed", "schema_validation"),
        ("WinError 2 not found", "process_start"),
        ("network connection reset", "network"),
        ("cancelled by user", "cancelled"),
        ("something odd happened", "unexpected"),
    ],
)
def test_copilot_exception_kind_mapping(message: str, kind: str) -> None:
    info = _classify_copilot_exception(RuntimeError(message), agent="security", batch_id="batch-001")

    assert info.kind == kind
    assert info.agent == "security"
    assert info.batch_id == "batch-001"
    assert "\n" not in info.message


def test_related_tests_avoid_short_stem_substring(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "src" / "api.py").write_text("def call():\n    return 1\n", encoding="utf-8")
    (repo / "tests" / "test_happy_path.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (repo / "tests" / "test_api.py").write_text("def test_api():\n    pass\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "related")

    analysis, _ = analyze_repository(resolve_repository(str(repo)), AuditOptions())

    api = next(item for item in analysis.target_files if item.path == "src/api.py")
    assert "tests/test_api.py" in api.related_tests
    assert "tests/test_happy_path.py" not in api.related_tests
