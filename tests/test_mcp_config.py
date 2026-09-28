from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_review.mcp_config import (
    MCP_SERVER_ID,
    McpConfigError,
    desired_server_config,
    install_mcp_config,
    mcp_config_path,
    mcp_manifest_path,
    status_mcp_config,
    sync_mcp_config,
    uninstall_mcp_config,
)


def _set_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "copilot-home"
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(home))
    return home


def test_install_mcp_config_preserves_other_servers(
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
                    "otherServer": {
                        "command": "other",
                        "args": ["serve"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = install_mcp_config()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["otherServer"]["command"] == "other"
    assert saved["mcpServers"][MCP_SERVER_ID] == desired_server_config()
    assert result["action"] == "installed"
    assert mcp_manifest_path().exists()
    assert status_mcp_config().status == "current"


def test_install_mcp_config_refuses_unmanaged_same_server(
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
                        "command": "custom-python",
                        "args": ["custom-server.py"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(McpConfigError, match="管理対象外"):
        install_mcp_config()


def test_sync_updates_managed_server_without_touching_others(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    install_mcp_config()

    path = mcp_config_path()
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["mcpServers"]["otherServer"] = {"command": "other"}
    saved["mcpServers"][MCP_SERVER_ID]["args"] = ["stale"]
    path.write_text(json.dumps(saved), encoding="utf-8")

    assert status_mcp_config().status == "outdated"

    result = sync_mcp_config()
    refreshed = json.loads(path.read_text(encoding="utf-8"))

    assert result["action"] == "updated"
    assert refreshed["mcpServers"][MCP_SERVER_ID] == desired_server_config()
    assert refreshed["mcpServers"]["otherServer"] == {"command": "other"}


def test_uninstall_mcp_config_removes_only_managed_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    install_mcp_config()

    path = mcp_config_path()
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["mcpServers"]["otherServer"] = {"command": "other"}
    path.write_text(json.dumps(saved), encoding="utf-8")

    result = uninstall_mcp_config()
    refreshed = json.loads(path.read_text(encoding="utf-8"))

    assert result["removed"] is True
    assert MCP_SERVER_ID not in refreshed["mcpServers"]
    assert refreshed["mcpServers"]["otherServer"] == {"command": "other"}
    assert not mcp_manifest_path().exists()


def test_uninstall_refuses_manually_modified_managed_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    install_mcp_config()

    path = mcp_config_path()
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["mcpServers"][MCP_SERVER_ID]["command"] = "changed"
    path.write_text(json.dumps(saved), encoding="utf-8")

    with pytest.raises(McpConfigError, match="手動変更"):
        uninstall_mcp_config()
