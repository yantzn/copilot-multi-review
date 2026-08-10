from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ai_review import cli
from ai_review.copilot import CopilotInfo, CopilotNotInstalledError, ensure_supported_python
from ai_review.repository import resolve_repository


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


def test_help_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    result = cli.main(["--help"])
    captured = capsys.readouterr()
    assert result == 0
    assert "validate-config" in captured.out
    assert "review" in captured.out


def test_storage_subcommands_are_registered(capsys: pytest.CaptureFixture[str]) -> None:
    result = cli.main(["--help"])
    captured = capsys.readouterr()
    assert result == 0
    assert "rerun" in captured.out
    assert "cleanup-locks" in captured.out


def test_validate_config_reports_missing_copilot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def raise_missing() -> CopilotInfo:
        raise CopilotNotInstalledError("missing copilot")

    monkeypatch.setattr(cli, "get_copilot_version", raise_missing)

    result = cli.main(["validate-config"])
    captured = capsys.readouterr()
    assert result == 2
    assert "missing copilot" in captured.err


def test_validate_config_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        cli,
        "get_copilot_version",
        lambda: CopilotInfo(executable="copilot", version="1.2.3"),
    )

    result = cli.main(["validate-config"])
    captured = capsys.readouterr()
    assert result == 0
    assert "設定検証に成功" in captured.out
    assert "1.2.3" in captured.out


def test_python_310_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 10, 12))
    with pytest.raises(RuntimeError, match="Python 3.11以上"):
        ensure_supported_python()


def test_required_subcommand_arguments() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["review", "--repo", "."])


def test_not_implemented_message() -> None:
    args = argparse.Namespace(command="cancel")
    with pytest.raises(cli.CommandNotImplementedError, match="cancel"):
        cli.handle_not_implemented(args)


def test_no_agents_preserves_requested_execution_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = tmp_path / "engine"
    engine.mkdir()
    repo = init_repo(tmp_path / "repo")
    monkeypatch.chdir(engine)

    result = cli.main(
        [
            "review",
            "--repo",
            str(repo),
            "--target",
            "base",
            "--execution-mode",
            "legacy",
            "--no-agents",
        ]
    )

    repository = resolve_repository(str(repo))
    latest = engine / "reports" / repository.project_id / "latest"
    run_json = json.loads((latest / "run.json").read_text(encoding="utf-8"))
    final_json = json.loads((latest / "final.json").read_text(encoding="utf-8"))

    assert result == 0
    assert run_json["request"]["execution_mode"] == "legacy"
    assert run_json["execution_mode"] == "legacy"
    assert final_json["execution_mode"] == "legacy"


def test_rerun_reuses_saved_execution_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = tmp_path / "engine"
    engine.mkdir()
    repo = init_repo(tmp_path / "repo")
    monkeypatch.chdir(engine)

    assert cli.main(
        [
            "review",
            "--repo",
            str(repo),
            "--target",
            "base",
            "--execution-mode",
            "legacy",
            "--no-agents",
        ]
    ) == 0

    captured: dict[str, object] = {}

    def capture_review(args: argparse.Namespace) -> int:
        captured["execution_mode"] = args.execution_mode
        captured["no_agents"] = args.no_agents
        return 0

    monkeypatch.setattr(cli, "handle_review", capture_review)

    result = cli.handle_rerun(argparse.Namespace(repo=str(repo), no_agents=True))

    assert result == 0
    assert captured == {"execution_mode": "legacy", "no_agents": True}
