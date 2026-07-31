from __future__ import annotations

import argparse
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


def test_review_is_explicitly_not_implemented(capsys: pytest.CaptureFixture[str]) -> None:
    result = cli.main(["show-latest", "--repo", "."])
    captured = capsys.readouterr()
    assert result == 4
    assert "後続Issueで実装" in captured.err


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
