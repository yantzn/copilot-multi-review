from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ai_review.antigravity_integration import (
    MCP_SERVER_ID,
    PLUGIN_NAME,
    AntigravityIntegrationError,
    antigravity_config_home,
    install_antigravity,
    installed_plugin_dir,
    manifest_path,
    mcp_config_path,
    source_plugin_dir,
    status_antigravity,
    sync_antigravity,
    uninstall_antigravity,
)


AGENTS = {
    "review-orchestrator",
    "requirements-reviewer",
    "correctness-reviewer",
    "design-conformance-reviewer",
    "project-rules-reviewer",
    "security-reviewer",
    "testing-reviewer",
    "maintainability-reviewer",
    "performance-reviewer",
    "operations-reviewer",
    "devil-advocate",
    "final-reviewer",
}


def _set_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "gemini-config"
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_ANTIGRAVITY_HOME", str(home))
    return home


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, frontmatter, _ = text.split("---", 2)
    value = yaml.safe_load(frontmatter)
    assert isinstance(value, dict)
    return value


def test_antigravity_plugin_has_expected_agents_and_review_skill() -> None:
    plugin = source_plugin_dir()
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == PLUGIN_NAME

    agent_files = {path.stem for path in (plugin / "agents").glob("*.md")}
    assert agent_files == AGENTS

    orchestrator = _frontmatter(plugin / "agents" / "review-orchestrator.md")
    assert orchestrator["name"] == "review-orchestrator"
    assert orchestrator["mainAgent"] is True
    assert orchestrator["subagent"] is True
    assert orchestrator["inheritMcp"] is True
    assert "invoke_subagent" in orchestrator["tools"]

    for name in AGENTS - {"review-orchestrator"}:
        metadata = _frontmatter(plugin / "agents" / f"{name}.md")
        assert metadata["mainAgent"] is False
        assert metadata["subagent"] is True
        assert metadata["inheritMcp"] is False
        tools = set(metadata.get("tools", []))
        assert "replace_file_content" not in tools
        assert "run_command" not in tools
        assert "invoke_subagent" not in tools

    skill = _frontmatter(plugin / "skills" / "review" / "SKILL.md")
    assert skill["name"] == "review"
    assert "コードレビュー" in str(skill["description"])


def test_install_antigravity_copies_plugin_and_preserves_other_mcp_servers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = _set_home(monkeypatch, tmp_path)
    config = mcp_config_path()
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "otherServer": {
                        "command": "other",
                        "args": ["serve"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = install_antigravity()

    assert result["plugin_action"] == "installed"
    assert result["mcp_action"] == "installed"
    assert installed_plugin_dir().is_dir()
    assert (installed_plugin_dir() / "agents" / "review-orchestrator.md").is_file()
    assert (installed_plugin_dir() / "skills" / "review" / "SKILL.md").is_file()
    assert manifest_path().is_file()

    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["otherServer"]["command"] == "other"
    server = saved["mcpServers"][MCP_SERVER_ID]
    assert server["args"] == ["-m", "ai_review.mcp_server"]
    assert server["cwd"] == "${workspaceFolder}"
    assert server["env"]["COPILOT_MULTI_REVIEW_OUTPUT_HOME"] == str(
        home / "copilot-multi-review"
    )

    status = status_antigravity()
    assert status.plugin_status == "current"
    assert status.mcp_status == "current"
    assert Path(status.plugin_dir).is_relative_to(home)


def test_sync_antigravity_restores_managed_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    install_antigravity()

    orchestrator = installed_plugin_dir() / "agents" / "review-orchestrator.md"
    orchestrator.write_text("stale", encoding="utf-8")
    assert status_antigravity().plugin_status == "outdated"

    result = sync_antigravity()

    assert result["plugin_action"] == "updated"
    assert status_antigravity().plugin_status == "current"
    assert orchestrator.read_text(encoding="utf-8") == (
        source_plugin_dir() / "agents" / "review-orchestrator.md"
    ).read_text(encoding="utf-8")


def test_install_antigravity_refuses_unmanaged_plugin_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    target = installed_plugin_dir()
    target.mkdir(parents=True)
    (target / "plugin.json").write_text('{"name": "foreign"}\n', encoding="utf-8")

    with pytest.raises(AntigravityIntegrationError, match="管理対象外"):
        install_antigravity()


def test_install_antigravity_refuses_unmanaged_mcp_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    path = mcp_config_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    MCP_SERVER_ID: {
                        "command": "custom",
                        "args": ["server.py"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AntigravityIntegrationError, match="管理対象外"):
        install_antigravity()


def test_uninstall_antigravity_removes_only_managed_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    install_antigravity()

    unrelated = antigravity_config_home() / "plugins" / "unrelated-plugin"
    unrelated.mkdir(parents=True)
    (unrelated / "plugin.json").write_text('{"name": "unrelated"}\n', encoding="utf-8")

    config = json.loads(mcp_config_path().read_text(encoding="utf-8"))
    config["mcpServers"]["otherServer"] = {"command": "other"}
    mcp_config_path().write_text(json.dumps(config), encoding="utf-8")

    result = uninstall_antigravity()

    assert result["plugin_removed"] is True
    assert result["mcp_removed"] is True
    assert not installed_plugin_dir().exists()
    assert unrelated.exists()
    saved = json.loads(mcp_config_path().read_text(encoding="utf-8"))
    assert MCP_SERVER_ID not in saved["mcpServers"]
    assert saved["mcpServers"]["otherServer"] == {"command": "other"}


def test_antigravity_review_contract_matches_core_vocabulary() -> None:
    plugin = source_plugin_dir()
    orchestrator = (plugin / "agents" / "review-orchestrator.md").read_text(encoding="utf-8")
    final = (plugin / "agents" / "final-reviewer.md").read_text(encoding="utf-8")

    for term in [
        "prepare_review",
        "finalize_review",
        "Critical",
        "Major",
        "Minor",
        "Info",
        "reviewer_states",
        "human_checks",
        "excluded_findings",
        "review_coverage",
    ]:
        assert term in orchestrator or term in final

    for decision in [
        "APPROVE",
        "APPROVE_WITH_NOTES",
        "CHANGES_REQUIRED",
        "BLOCKED",
        "INCONCLUSIVE",
    ]:
        assert decision in final
