from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess
import sys


class CopilotError(RuntimeError):
    """Base error for Copilot CLI checks."""


class CopilotNotInstalledError(CopilotError):
    """Raised when GitHub Copilot CLI cannot be found."""


class CopilotAuthError(CopilotError):
    """Raised when GitHub Copilot CLI appears to be unauthenticated."""


@dataclass(frozen=True)
class CopilotInfo:
    executable: str
    version: str


def find_copilot_executable() -> str:
    executable = shutil.which("copilot")
    if not executable:
        raise CopilotNotInstalledError(
            "GitHub Copilot CLIが見つかりません。GitHub CLIのCopilot拡張を導入し、"
            "`copilot version`が実行できる状態にしてください。"
        )
    return executable


def get_copilot_version(timeout_seconds: int = 10) -> CopilotInfo:
    executable = find_copilot_executable()
    try:
        completed = subprocess.run(
            [executable, "version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except OSError as exc:
        raise CopilotError(f"GitHub Copilot CLIを起動できません: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CopilotError("GitHub Copilot CLIのversion確認がタイムアウトしました。") from exc

    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        lowered = output.lower()
        if "auth" in lowered or "login" in lowered:
            raise CopilotAuthError(
                "GitHub Copilot CLIの認証が必要です。`copilot login`等で認証してから再実行してください。"
            )
        raise CopilotError(f"GitHub Copilot CLIのversion確認に失敗しました: {output}")

    return CopilotInfo(executable=executable, version=output or "unknown")


def ensure_supported_python() -> None:
    if sys.version_info < (3, 11):
        major = sys.version_info[0]
        minor = sys.version_info[1]
        raise RuntimeError(
            "copilot-multi-reviewはPython 3.11以上が必要です。"
            f"現在のPython: {major}.{minor}"
        )
