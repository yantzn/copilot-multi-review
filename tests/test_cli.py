from __future__ import annotations

import argparse
from types import SimpleNamespace
import sys

import pytest

from ai_review import cli
from ai_review.copilot import CopilotInfo, CopilotNotInstalledError, ensure_supported_python


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


def test_audit_options_use_common_config_and_cli_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "_repository_audit_config",
        lambda: {
            "profile": "quick",
            "max_batches": 7,
            "max_files": 8,
            "max_total_lines": 9,
            "max_copilot_calls": 10,
            "max_files_per_batch": 2,
            "max_lines_per_batch": 3,
            "max_chars_per_batch": 4,
            "max_file_bytes": 5,
        },
    )
    args = argparse.Namespace(
        profile=None,
        include_untracked=False,
        max_batches=11,
        max_files=None,
        max_total_lines=None,
        max_copilot_calls=None,
        max_files_per_batch=None,
        max_lines_per_batch=None,
        max_chars_per_batch=None,
        max_file_bytes=None,
        rerun=False,
        no_agents=False,
    )

    options = cli._audit_options_from_args(args)

    assert options.profile == "quick"
    assert options.max_batches == 11
    assert options.max_files == 8
    assert options.max_chars_per_batch == 4


def test_audit_exit_codes() -> None:
    base = {"execution_mode": "review", "cancelled_batches": [], "final_decision": "APPROVE"}

    assert cli._audit_exit_code(SimpleNamespace(**base)) == 0
    assert cli._audit_exit_code(SimpleNamespace(**(base | {"final_decision": "APPROVE_WITH_NOTES"}))) == 0
    assert cli._audit_exit_code(SimpleNamespace(**(base | {"final_decision": "CHANGES_REQUIRED"}))) == 1
    assert cli._audit_exit_code(SimpleNamespace(**(base | {"final_decision": "BLOCKED"}))) == 2
    assert cli._audit_exit_code(SimpleNamespace(**(base | {"final_decision": "INCONCLUSIVE"}))) == 3
    assert cli._audit_exit_code(SimpleNamespace(**(base | {"cancelled_batches": ["batch-001"]}))) == 4
    assert cli._audit_exit_code(SimpleNamespace(**(base | {"execution_mode": "analysis_only", "final_decision": "ANALYSIS_ONLY"}))) == 0


def test_status_watch_prints_only_when_changed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    statuses = [
        [{"status": "running", "run_id": "run-1"}],
        [{"status": "running", "run_id": "run-1"}],
        [{"status": "completed", "run_id": "run-1"}],
    ]

    def fake_statuses(_paths, _project_id=None):
        return statuses.pop(0)

    monkeypatch.setattr(cli, "load_running_statuses", fake_statuses)
    monkeypatch.setattr(cli, "format_running_status", lambda status: f"{status['run_id']}:{status['status']}")
    monkeypatch.setattr(cli.time, "sleep", lambda _interval: None)

    result = cli.handle_status(argparse.Namespace(repo=None, watch=True, interval=1.0))
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out.count("run-1:running") == 1
    assert captured.out.count("run-1:completed") == 1
