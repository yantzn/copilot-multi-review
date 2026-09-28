from __future__ import annotations

from pathlib import Path

import pytest

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
