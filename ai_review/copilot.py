from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from .processes import decode_output


class CopilotError(RuntimeError):
    """Base error for Copilot CLI checks."""


class CopilotNotInstalledError(CopilotError):
    """Raised when GitHub Copilot CLI cannot be found."""


class CopilotAuthError(CopilotError):
    """Raised when GitHub Copilot CLI appears to be unauthenticated."""


class CopilotStartupError(CopilotError):
    """Raised when GitHub Copilot CLI cannot be launched."""


@dataclass(frozen=True)
class CopilotInfo:
    executable: str
    version: str


@dataclass(frozen=True)
class CopilotCommand:
    resolved_path: str
    command: list[str]


def find_copilot_executable() -> str:
    executable = resolve_copilot_command([]).resolved_path
    return executable


def resolve_copilot_command(args: list[str]) -> CopilotCommand:
    executable = _find_copilot_path()
    suffix = Path(executable).suffix.lower()
    if platform.system() == "Windows" and suffix in {".bat", ".cmd"}:
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return CopilotCommand(
            resolved_path=executable,
            command=[comspec, "/d", "/c", "call", executable, *args],
        )
    return CopilotCommand(resolved_path=executable, command=[executable, *args])


def _find_copilot_path() -> str:
    candidates = ["copilot.exe", "copilot.cmd", "copilot.bat", "copilot"] if platform.system() == "Windows" else ["copilot"]
    executable = next((path for candidate in candidates if (path := shutil.which(candidate))), None)
    if not executable:
        raise CopilotNotInstalledError(
            "GitHub Copilot CLIが見つかりません。GitHub CLIのCopilot拡張を導入し、"
            "`copilot version`が実行できる状態にしてください。"
        )
    return executable


def get_copilot_version(timeout_seconds: int = 10) -> CopilotInfo:
    command = resolve_copilot_command(["version"])
    try:
        completed = subprocess.run(
            command.command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            shell=False,
        )
    except OSError as exc:
        raise CopilotStartupError(f"GitHub Copilot CLIを起動できません: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CopilotError("GitHub Copilot CLIのversion確認がタイムアウトしました。") from exc

    output = (decode_output(completed.stdout) or decode_output(completed.stderr) or "").strip()
    if completed.returncode != 0:
        category = classify_copilot_error(output)
        if category == "auth":
            raise CopilotAuthError(
                "GitHub Copilot CLIの認証が必要です。`copilot login`等で認証してから再実行してください。"
            )
        raise CopilotError(f"GitHub Copilot CLIのversion確認に失敗しました ({category}): {output}")

    return CopilotInfo(executable=command.resolved_path, version=output or "unknown")


def classify_copilot_error(output: str) -> str:
    lowered = output.lower()
    if "auth" in lowered or "login" in lowered or "unauthorized" in lowered:
        return "auth"
    if "permission" in lowered or "forbidden" in lowered:
        return "permission"
    if "rate limit" in lowered or "too many requests" in lowered:
        return "rate_limit"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "network" in lowered or "dns" in lowered or "connection" in lowered:
        return "network"
    if "not found" in lowered or "no such file" in lowered:
        return "not_installed"
    return "other"


def ensure_supported_python() -> None:
    if sys.version_info < (3, 11):
        major = sys.version_info[0]
        minor = sys.version_info[1]
        raise RuntimeError(
            "copilot-multi-reviewはPython 3.11以上が必要です。"
            f"現在のPython: {major}.{minor}"
        )
