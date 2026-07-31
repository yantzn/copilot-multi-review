from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from ai_review.processes import decode_output
from ai_review.quality import UnsafeCommandError, parse_safe_command, run_quality_checks


def test_parse_safe_command_rejects_shell_constructs() -> None:
    with pytest.raises(UnsafeCommandError):
        parse_safe_command("python -m pytest | more")


def test_parse_safe_command_rejects_git_writes() -> None:
    with pytest.raises(UnsafeCommandError):
        parse_safe_command("git reset --hard")


def test_parse_safe_command_allows_known_command() -> None:
    assert parse_safe_command("python -m pytest tests") == ["python", "-m", "pytest", "tests"]


def test_quality_failure_is_failed(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n", encoding="utf-8")
    result = run_quality_checks(tmp_path, ["python -m pytest missing"], timeout_seconds=30)[0]

    assert result.status == "failed"
    assert result.returncode != 0


def test_decode_output_supports_cp932() -> None:
    assert decode_output("日本語".encode("cp932")) == "日本語"


def test_decode_output_replaces_invalid_bytes() -> None:
    assert "\ufffd" in decode_output(b"\xff\xfe\xfa")
