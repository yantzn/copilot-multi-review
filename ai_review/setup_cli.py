from __future__ import annotations

import argparse

from .custom_agent_installer import (
    CustomAgentInstallError,
    install_agents,
    status_agents,
    sync_agents,
    uninstall_agents,
)
from .mcp_config import (
    McpConfigError,
    install_mcp_config,
    status_mcp_config,
    sync_mcp_config,
    uninstall_mcp_config,
)


STATUS_LABELS = {
    "current": "最新",
    "outdated": "更新あり",
    "missing": "未インストール",
    "unmanaged_conflict": "管理外ファイルと競合",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="copilot-multi-review",
        description="copilot-multi-review のUser Custom AgentとMCP server設定を管理します。",
    )
    subparsers = parser.add_subparsers(dest="command")

    install = subparsers.add_parser("install", help="User Custom AgentとMCP serverを初回インストールします")
    install.set_defaults(func=_handle_install)

    sync = subparsers.add_parser("sync", help="User Custom AgentとMCP server設定を同期します")
    sync.set_defaults(func=_handle_sync)

    status = subparsers.add_parser("status", help="User Custom AgentとMCP serverの状態を表示します")
    status.set_defaults(func=_handle_status)

    uninstall = subparsers.add_parser("uninstall", help="本ツール管理下のUser Custom AgentとMCP server設定を削除します")
    uninstall.set_defaults(func=_handle_uninstall)
    return parser


def _print_sync_result(action: str, result: dict[str, object]) -> None:
    print(f"{action}しました。")
    print(f"Source: {result['source_dir']}")
    print(f"Destination: {result['agents_dir']}")
    print(f"Installed: {len(result['installed'])}")
    print(f"Updated: {len(result['updated'])}")
    print(f"Unchanged: {len(result['unchanged'])}")
    print(f"Removed: {len(result['removed'])}")
    print(f"Total: {result['total']}")


def _print_mcp_result(action: str, result: dict[str, object]) -> None:
    print(f"MCP serverを{action}しました。")
    print(f"Server: {result['server_id']}")
    print(f"Config: {result['config_path']}")
    if "action" in result:
        print(f"Action: {result['action']}")


def _handle_install(_args: argparse.Namespace) -> int:
    _print_sync_result("Custom Agentをインストール", install_agents())
    _print_mcp_result("インストール", install_mcp_config())
    return 0


def _handle_sync(_args: argparse.Namespace) -> int:
    _print_sync_result("Custom Agentを同期", sync_agents())
    _print_mcp_result("同期", sync_mcp_config())
    return 0


def _handle_status(_args: argparse.Namespace) -> int:
    statuses = status_agents()
    print("Custom Agent status:")
    for item in statuses:
        label = STATUS_LABELS.get(item.status, item.status)
        print(f"- {item.installed_name}: {label}")
    current = sum(1 for item in statuses if item.status == "current")
    print(f"Current: {current}/{len(statuses)}")

    mcp_status = status_mcp_config()
    mcp_label = STATUS_LABELS.get(mcp_status.status, mcp_status.status)
    print("MCP status:")
    print(f"- {mcp_status.server_id}: {mcp_label}")
    print(f"- config: {mcp_status.config_path}")
    return 0


def _handle_uninstall(_args: argparse.Namespace) -> int:
    mcp_result = uninstall_mcp_config()
    result = uninstall_agents()
    print("本ツール管理下のCustom Agentを削除しました。")
    print(f"Destination: {result['agents_dir']}")
    print(f"Removed: {result['total']}")
    print("本ツール管理下のMCP server設定を削除しました。")
    print(f"Server: {mcp_result['server_id']}")
    print(f"Removed: {mcp_result['removed']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except (CustomAgentInstallError, McpConfigError) as exc:
        print(f"エラー: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
