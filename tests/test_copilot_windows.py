from __future__ import annotations

import subprocess

import pytest

from ai_review import copilot


def test_windows_cmd_uses_comspec_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(copilot.platform, "system", lambda: "Windows")
    monkeypatch.setattr(copilot.shutil, "which", lambda name: "C:\\Program Files\\GitHub\\copilot.cmd" if name == "copilot.cmd" else None)
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\System32\\cmd.exe")

    command = copilot.resolve_copilot_command(["version"])

    assert command.command == [
        "C:\\Windows\\System32\\cmd.exe",
        "/d",
        "/c",
        "call",
        "C:\\Program Files\\GitHub\\copilot.cmd",
        "version",
    ]


def test_windows_bat_falls_back_when_comspec_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(copilot.platform, "system", lambda: "Windows")
    monkeypatch.setattr(copilot.shutil, "which", lambda name: "C:\\Tools\\copilot.bat" if name == "copilot.bat" else None)
    monkeypatch.delenv("COMSPEC", raising=False)

    command = copilot.resolve_copilot_command(["version"])

    assert command.command[:4] == ["cmd.exe", "/d", "/c", "call"]


def test_windows_exe_runs_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(copilot.platform, "system", lambda: "Windows")
    monkeypatch.setattr(copilot.shutil, "which", lambda name: "C:\\Tools\\copilot.exe" if name == "copilot.exe" else None)

    command = copilot.resolve_copilot_command(["version"])

    assert command.command == ["C:\\Tools\\copilot.exe", "version"]


def test_posix_runs_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(copilot.platform, "system", lambda: "Linux")
    monkeypatch.setattr(copilot.shutil, "which", lambda name: "/usr/local/bin/copilot")

    command = copilot.resolve_copilot_command(["version"])

    assert command.command == ["/usr/local/bin/copilot", "version"]


def test_version_decodes_cp932(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(copilot.platform, "system", lambda: "Windows")
    monkeypatch.setattr(copilot.shutil, "which", lambda name: "C:\\Tools\\copilot.exe" if name == "copilot.exe" else None)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="バージョン 1".encode("cp932"), stderr=b"")

    monkeypatch.setattr(copilot.subprocess, "run", fake_run)

    assert copilot.get_copilot_version().version == "バージョン 1"


def test_error_classification() -> None:
    assert copilot.classify_copilot_error("rate limit exceeded") == "rate_limit"
    assert copilot.classify_copilot_error("please login") == "auth"
    assert copilot.classify_copilot_error("connection reset") == "network"
