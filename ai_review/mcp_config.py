from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

from .custom_agent_installer import copilot_home, metadata_dir


MCP_SERVER_ID = "copilotMultiReview"
MCP_MANIFEST_VERSION = 1


class McpConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpStatus:
    server_id: str
    status: str
    config_path: str
    command: str | None


def mcp_config_path() -> Path:
    return copilot_home() / "mcp-config.json"


def mcp_manifest_path() -> Path:
    return metadata_dir() / "mcp-manifest.json"


def desired_server_config() -> dict[str, object]:
    return {
        "command": sys.executable,
        "args": ["-m", "ai_review.mcp_server"],
        "cwd": "${workspaceFolder}",
    }


def install_mcp_config() -> dict[str, object]:
    return sync_mcp_config(require_managed=False)


def sync_mcp_config(*, require_managed: bool = True) -> dict[str, object]:
    path = mcp_config_path()
    config = _load_config(path)
    servers = _servers(config)
    manifest = _load_manifest()
    current = servers.get(MCP_SERVER_ID)
    desired = desired_server_config()

    if current is not None and not manifest:
        raise McpConfigError(
            f"管理対象外の同名MCP serverと衝突しています: {MCP_SERVER_ID}"
        )
    if require_managed and not manifest and current is None:
        # sync can also repair a missing configuration after a previous install
        # only when the manifest still exists. Without a manifest, ownership is
        # ambiguous, so require install first.
        raise McpConfigError(
            "MCP serverは未インストールです。先に copilot-multi-review install を実行してください。"
        )

    action = "unchanged"
    if current != desired:
        servers[MCP_SERVER_ID] = desired
        _write_config(path, config)
        action = "installed" if current is None else "updated"
    elif not path.exists():
        _write_config(path, config)
        action = "installed"

    metadata_dir().mkdir(parents=True, exist_ok=True)
    _write_manifest(
        {
            "manifest_version": MCP_MANIFEST_VERSION,
            "server_id": MCP_SERVER_ID,
            "config_path": str(path),
            "server": desired,
        }
    )

    return {
        "server_id": MCP_SERVER_ID,
        "config_path": str(path),
        "action": action,
        "server": desired,
    }


def status_mcp_config() -> McpStatus:
    path = mcp_config_path()
    config = _load_config(path)
    servers = _servers(config)
    current = servers.get(MCP_SERVER_ID)
    manifest = _load_manifest()
    desired = desired_server_config()

    if current is None:
        state = "missing"
        command = None
    elif not manifest:
        state = "unmanaged_conflict"
        command = _command(current)
    elif current == desired:
        state = "current"
        command = _command(current)
    else:
        state = "outdated"
        command = _command(current)

    return McpStatus(
        server_id=MCP_SERVER_ID,
        status=state,
        config_path=str(path),
        command=command,
    )


def uninstall_mcp_config() -> dict[str, object]:
    path = mcp_config_path()
    config = _load_config(path)
    servers = _servers(config)
    manifest = _load_manifest()

    if not manifest:
        return {
            "server_id": MCP_SERVER_ID,
            "config_path": str(path),
            "removed": False,
        }

    current = servers.get(MCP_SERVER_ID)
    manifest_server = manifest.get("server")
    if current is not None and current != manifest_server:
        raise McpConfigError(
            "管理対象MCP serverの設定が手動変更されています。安全のため自動削除しません。"
        )

    removed = current is not None
    if removed:
        del servers[MCP_SERVER_ID]
        _write_config(path, config)

    manifest_file = mcp_manifest_path()
    if manifest_file.exists():
        manifest_file.unlink()
    meta = metadata_dir()
    if meta.exists() and not any(meta.iterdir()):
        meta.rmdir()

    return {
        "server_id": MCP_SERVER_ID,
        "config_path": str(path),
        "removed": removed,
    }


def _command(value: object) -> str | None:
    if isinstance(value, dict):
        command = value.get("command")
        return command if isinstance(command, str) else None
    return None


def _load_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"mcpServers": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpConfigError(f"MCP configを読み込めません: {path}") from exc
    if not isinstance(value, dict):
        raise McpConfigError(f"MCP configのトップレベルはobjectである必要があります: {path}")
    if "mcpServers" not in value:
        value["mcpServers"] = {}
    _servers(value)
    return value


def _servers(config: dict[str, object]) -> dict[str, object]:
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        raise McpConfigError("mcpServersはobjectである必要があります。")
    return servers


def _write_config(path: Path, config: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_manifest() -> dict[str, object]:
    path = mcp_manifest_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpConfigError(f"MCP manifestを読み込めません: {path}") from exc
    if not isinstance(value, dict):
        raise McpConfigError(f"MCP manifestの形式が不正です: {path}")
    return value


def _write_manifest(payload: dict[str, object]) -> None:
    path = mcp_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
