from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


PLUGIN_NAME = "copilot-multi-review"
MCP_SERVER_ID = "copilotMultiReview"
MANIFEST_VERSION = 1


class AntigravityIntegrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AntigravityStatus:
    plugin_status: str
    mcp_status: str
    plugin_dir: str
    mcp_config_path: str


def antigravity_config_home() -> Path:
    override = os.environ.get("COPILOT_MULTI_REVIEW_ANTIGRAVITY_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".gemini" / "config").resolve()


def source_plugin_dir() -> Path:
    return Path(__file__).resolve().parent / "antigravity_plugin"


def installed_plugin_dir() -> Path:
    return antigravity_config_home() / "plugins" / PLUGIN_NAME


def mcp_config_path() -> Path:
    return antigravity_config_home() / "mcp_config.json"


def metadata_dir() -> Path:
    return antigravity_config_home() / "copilot-multi-review"


def manifest_path() -> Path:
    return metadata_dir() / "antigravity-manifest.json"


def desired_mcp_server_config() -> dict[str, object]:
    return {
        "command": sys.executable,
        "args": ["-m", "ai_review.mcp_server"],
        "cwd": "${workspaceFolder}",
        "env": {
            "COPILOT_MULTI_REVIEW_OUTPUT_HOME": str(
                antigravity_config_home() / "copilot-multi-review"
            )
        },
    }


def install_antigravity() -> dict[str, object]:
    return _sync_antigravity(require_managed=False)


def sync_antigravity() -> dict[str, object]:
    return _sync_antigravity(require_managed=True)


def status_antigravity() -> AntigravityStatus:
    manifest = _load_manifest()
    source = source_plugin_dir()
    _validate_source_plugin(source)
    target = installed_plugin_dir()

    if not target.exists():
        plugin_status = "missing"
    elif not manifest:
        plugin_status = "unmanaged_conflict"
    elif _tree_digest(target) == _tree_digest(source):
        plugin_status = "current"
    else:
        plugin_status = "outdated"

    config = _load_mcp_config()
    current = _servers(config).get(MCP_SERVER_ID)
    desired = desired_mcp_server_config()
    if current is None:
        mcp_status = "missing"
    elif not manifest:
        mcp_status = "unmanaged_conflict"
    elif current == desired:
        mcp_status = "current"
    else:
        mcp_status = "outdated"

    return AntigravityStatus(
        plugin_status=plugin_status,
        mcp_status=mcp_status,
        plugin_dir=str(target),
        mcp_config_path=str(mcp_config_path()),
    )


def uninstall_antigravity() -> dict[str, object]:
    manifest = _load_manifest()
    if not manifest:
        return {
            "plugin_removed": False,
            "mcp_removed": False,
            "plugin_dir": str(installed_plugin_dir()),
            "mcp_config_path": str(mcp_config_path()),
        }

    target = installed_plugin_dir()
    expected_digest = manifest.get("plugin_digest")
    if target.exists():
        current_digest = _tree_digest(target)
        if expected_digest and current_digest != expected_digest:
            raise AntigravityIntegrationError(
                "管理対象Antigravity Pluginが手動変更されています。安全のため自動削除しません。"
            )

    config = _load_mcp_config()
    servers = _servers(config)
    current_server = servers.get(MCP_SERVER_ID)
    expected_server = manifest.get("mcp_server")
    if current_server is not None and current_server != expected_server:
        raise AntigravityIntegrationError(
            "管理対象Antigravity MCP設定が手動変更されています。安全のため自動削除しません。"
        )

    plugin_removed = target.exists()
    if plugin_removed:
        shutil.rmtree(target)

    mcp_removed = current_server is not None
    if mcp_removed:
        del servers[MCP_SERVER_ID]
        _write_mcp_config(config)

    path = manifest_path()
    if path.exists():
        path.unlink()
    meta = metadata_dir()
    if meta.exists() and not any(meta.iterdir()):
        meta.rmdir()

    return {
        "plugin_removed": plugin_removed,
        "mcp_removed": mcp_removed,
        "plugin_dir": str(target),
        "mcp_config_path": str(mcp_config_path()),
    }


def _sync_antigravity(*, require_managed: bool) -> dict[str, object]:
    source = source_plugin_dir()
    _validate_source_plugin(source)
    target = installed_plugin_dir()
    manifest = _load_manifest()

    if target.exists() and not manifest:
        raise AntigravityIntegrationError(
            f"管理対象外のAntigravity Pluginと衝突しています: {target}"
        )
    config = _load_mcp_config()
    servers = _servers(config)
    current_server = servers.get(MCP_SERVER_ID)
    if current_server is not None and not manifest:
        raise AntigravityIntegrationError(
            f"管理対象外の同名Antigravity MCP serverと衝突しています: {MCP_SERVER_ID}"
        )
    if require_managed and not manifest and not target.exists() and current_server is None:
        raise AntigravityIntegrationError(
            "Antigravity integrationは未インストールです。"
            "先に copilot-multi-review install --platform antigravity を実行してください。"
        )

    source_digest = _tree_digest(source)
    plugin_action = "unchanged"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        plugin_action = "installed"
    elif _tree_digest(target) != source_digest:
        shutil.rmtree(target)
        shutil.copytree(source, target)
        plugin_action = "updated"

    desired_server = desired_mcp_server_config()
    mcp_action = "unchanged"
    if current_server != desired_server:
        servers[MCP_SERVER_ID] = desired_server
        _write_mcp_config(config)
        mcp_action = "installed" if current_server is None else "updated"
    elif not mcp_config_path().exists():
        _write_mcp_config(config)
        mcp_action = "installed"

    metadata_dir().mkdir(parents=True, exist_ok=True)
    _write_manifest(
        {
            "manifest_version": MANIFEST_VERSION,
            "plugin_name": PLUGIN_NAME,
            "plugin_dir": str(target),
            "plugin_digest": source_digest,
            "mcp_server_id": MCP_SERVER_ID,
            "mcp_config_path": str(mcp_config_path()),
            "mcp_server": desired_server,
        }
    )

    return {
        "plugin_action": plugin_action,
        "mcp_action": mcp_action,
        "plugin_dir": str(target),
        "mcp_config_path": str(mcp_config_path()),
        "server_id": MCP_SERVER_ID,
    }


def _validate_source_plugin(path: Path) -> None:
    required = [
        path / "plugin.json",
        path / "agents" / "review-orchestrator.md",
        path / "agents" / "requirements-reviewer.md",
        path / "agents" / "correctness-reviewer.md",
        path / "agents" / "design-conformance-reviewer.md",
        path / "agents" / "project-rules-reviewer.md",
        path / "agents" / "security-reviewer.md",
        path / "agents" / "testing-reviewer.md",
        path / "agents" / "maintainability-reviewer.md",
        path / "agents" / "performance-reviewer.md",
        path / "agents" / "operations-reviewer.md",
        path / "agents" / "devil-advocate.md",
        path / "agents" / "final-reviewer.md",
        path / "skills" / "review" / "SKILL.md",
    ]
    missing = [str(item.relative_to(path)) for item in required if not item.is_file()]
    if missing:
        raise AntigravityIntegrationError(
            "Antigravity Pluginテンプレートが不足しています: " + ", ".join(missing)
        )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_mcp_config() -> dict[str, object]:
    path = mcp_config_path()
    if not path.exists():
        return {"mcpServers": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AntigravityIntegrationError(f"Antigravity MCP configを読み込めません: {path}") from exc
    if not isinstance(value, dict):
        raise AntigravityIntegrationError(
            f"Antigravity MCP configのトップレベルはobjectである必要があります: {path}"
        )
    if "mcpServers" not in value:
        value["mcpServers"] = {}
    _servers(value)
    return value


def _servers(config: dict[str, object]) -> dict[str, object]:
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        raise AntigravityIntegrationError("Antigravity mcpServersはobjectである必要があります。")
    return servers


def _write_mcp_config(config: dict[str, object]) -> None:
    path = mcp_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_manifest() -> dict[str, object]:
    path = manifest_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AntigravityIntegrationError(
            f"Antigravity integration manifestを読み込めません: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise AntigravityIntegrationError(
            f"Antigravity integration manifestの形式が不正です: {path}"
        )
    return value


def _write_manifest(payload: dict[str, object]) -> None:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
