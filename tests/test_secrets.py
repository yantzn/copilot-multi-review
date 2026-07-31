from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess

from ai_review.diff_collector import collect_diff
from ai_review.quality import QualityCheckResult
from ai_review.repository import resolve_repository
from ai_review.review_engine import EngineRequest, run_review_engine
from ai_review.secrets import scan_diff_for_secrets


class FakeClient:
    provider = "github-copilot-cli"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
        self.calls.append(prompt)
        raise AssertionError("Copilot should not be called")


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


def test_confirmed_secret_blocks_before_copilot(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / ".env").write_text("API_KEY=sk_live_12345678901234567890\n", encoding="utf-8")
    repository = resolve_repository(str(repo))
    diff = collect_diff(repository, target="uncommitted")
    client = FakeClient()

    result = run_review_engine(
        EngineRequest(
            repository=repository,
            diff=diff,
            quality_checks=[QualityCheckResult(name="quality", command=[], status="skipped")],
            target="uncommitted",
            run_id="run-1",
        ),
        client,
    )

    assert result.final_decision == "BLOCKED"
    assert result.max_concurrent_copilot_processes == 0
    assert client.calls == []


def test_detector_definition_is_non_blocking() -> None:
    diff = collect_diff.__annotations__  # keep import used in a harmless way
    _ = diff
    from ai_review.diff_collector import DiffSummary

    summary = DiffSummary(
        target="uncommitted",
        changed_files=[],
        diff_text="+++ b/ai_review/secrets.py\n+PATTERN = re.compile(r'-----BEGIN PRIVATE KEY-----')\n",
        changed_file_count=1,
        diff_line_count=1,
        truncated=False,
    )

    result = scan_diff_for_secrets(summary)

    assert result.findings[0].classification == "detector_definition"
    assert result.blocked is False


def test_test_fixture_is_non_blocking_but_real_pem_blocks() -> None:
    from ai_review.diff_collector import DiffSummary

    fixture = DiffSummary(
        target="uncommitted",
        changed_files=[],
        diff_text="+++ b/tests/fixtures/test_secret.py\n+dummy_fixture = '-----BEGIN CERTIFICATE-----'\n",
        changed_file_count=1,
        diff_line_count=1,
        truncated=False,
    )
    real = replace(
        fixture,
        diff_text="+++ b/app.py\n+CERT = '''-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----'''\n",
    )

    assert scan_diff_for_secrets(fixture).blocked is False
    assert scan_diff_for_secrets(real).blocked is True


def test_secret_value_is_not_stored() -> None:
    from ai_review.diff_collector import DiffSummary

    summary = DiffSummary(
        target="uncommitted",
        changed_files=[],
        diff_text="+++ b/.env\n+GITHUB_TOKEN=ghp_123456789012345678901234567890123456\n",
        changed_file_count=1,
        diff_line_count=1,
        truncated=False,
    )

    finding = scan_diff_for_secrets(summary).findings[0]

    assert "ghp_" not in repr(finding)
    assert finding.fingerprint
