from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import sys

from .processes import decode_output, run_command


class UnsafeCommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class QualityCheckResult:
    name: str
    command: list[str]
    status: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""


FORBIDDEN_TOKENS = {"|", ">", ">>", "<", "$(", "`", "&&", "||", ";"}
FORBIDDEN_COMMANDS = {"git fetch", "git checkout", "git reset"}
ALLOWLIST = {
    "python -m pytest",
    "python -m ruff check",
    "python -m mypy",
    "npm test",
}


def detect_quality_commands(root: Path, explicit: list[str] | None = None) -> list[list[str]]:
    if explicit:
        return [parse_safe_command(command) for command in explicit]
    if (root / "pyproject.toml").exists():
        return [["python", "-m", "pytest"]]
    if (root / "package.json").exists():
        return [["npm", "test"]]
    return []


def run_quality_checks(root: Path, explicit: list[str] | None = None, *, timeout_seconds: int = 120) -> list[QualityCheckResult]:
    commands = detect_quality_commands(root, explicit)
    if not commands:
        return [QualityCheckResult(name="quality", command=[], status="skipped")]
    results: list[QualityCheckResult] = []
    for command in commands:
        name = " ".join(command)
        executable_command = [sys.executable, *command[1:]] if command[0] == "python" else command
        completed = run_command(executable_command, cwd=root, timeout_seconds=timeout_seconds)
        status = "passed" if completed.returncode == 0 else "failed"
        results.append(
            QualityCheckResult(
                name=name,
                command=command,
                status=status,
                returncode=completed.returncode,
                stdout=_truncate(decode_output(completed.stdout)),
                stderr=_truncate(decode_output(completed.stderr)),
            )
        )
    return results


def parse_safe_command(command: str) -> list[str]:
    if any(token in command for token in FORBIDDEN_TOKENS):
        raise UnsafeCommandError("パイプ、リダイレクト、コマンド置換、式評価は使用できません。")
    parts = shlex.split(command, posix=False)
    if not parts:
        raise UnsafeCommandError("空の品質チェックコマンドは使用できません。")
    normalized = " ".join(parts[:3] if parts[:2] == ["python", "-m"] else parts[:2])
    if any(" ".join(parts[:2]) == forbidden or " ".join(parts[:3]) == forbidden for forbidden in FORBIDDEN_COMMANDS):
        raise UnsafeCommandError("fetch、checkout、resetは禁止されています。")
    if not any(" ".join(parts[: len(allowed.split())]) == allowed for allowed in ALLOWLIST):
        raise UnsafeCommandError("allowlistにない品質チェックコマンドは実行できません。")
    return parts


def _truncate(value: str, limit: int = 20_000) -> str:
    return value if len(value) <= limit else value[:limit] + "\n[truncated]"
