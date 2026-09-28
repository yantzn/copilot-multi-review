from __future__ import annotations

from pathlib import Path

import pytest

from ai_review.antigravity_integration import installed_plugin_dir as antigravity_plugin_dir
from ai_review.antigravity_integration import mcp_config_path as antigravity_mcp_config_path
from ai_review.custom_agent_installer import user_agents_dir
from ai_review.mcp_config import MCP_SERVER_ID, mcp_config_path
from ai_review.setup_cli import main


def test_setup_cli_manages_agents_and_mcp_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(tmp_path / "copilot-home"))

    assert main(["install"]) == 0
    assert user_agents_dir().exists()
    assert mcp_config_path().exists()
    assert MCP_SERVER_ID in mcp_config_path().read_text(encoding="utf-8")

    assert main(["status"]) == 0
    output = capsys.readouterr().out
    assert "MCP status:" in output
    assert f"- {MCP_SERVER_ID}: 最新" in output

    assert main(["sync"]) == 0
    assert main(["uninstall"]) == 0
    assert not any(user_agents_dir().glob("copilot-multi-review-*.agent.md"))


def test_setup_cli_manages_antigravity_platform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "COPILOT_MULTI_REVIEW_ANTIGRAVITY_HOME",
        str(tmp_path / "gemini-config"),
    )

    assert main(["install", "--platform", "antigravity"]) == 0
    assert antigravity_plugin_dir().is_dir()
    assert antigravity_mcp_config_path().is_file()

    assert main(["status", "--platform", "antigravity"]) == 0
    output = capsys.readouterr().out
    assert "Antigravity status:" in output
    assert "- plugin: 最新" in output
    assert "- MCP: 最新" in output

    assert main(["sync", "--platform", "antigravity"]) == 0
    assert main(["uninstall", "--platform", "antigravity"]) == 0
    assert not antigravity_plugin_dir().exists()


def test_setup_cli_platform_default_remains_copilot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(tmp_path / "copilot-home"))
    monkeypatch.setenv(
        "COPILOT_MULTI_REVIEW_ANTIGRAVITY_HOME",
        str(tmp_path / "gemini-config"),
    )

    assert main(["install"]) == 0
    assert user_agents_dir().exists()
    assert not antigravity_plugin_dir().exists()
