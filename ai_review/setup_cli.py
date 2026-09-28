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
from .antigravity_integration import (
    AntigravityIntegrationError,
    install_antigravity,
    status_antigravity,
    sync_antigravity,
    uninstall_antigravity,
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
        description="copilot-multi-review のCopilot / Antigravity integrationを管理します。",
    )
    subparsers = parser.add_subparsers(dest="command")

    install = subparsers.add_parser("install", help="レビューintegrationを初回インストールします")
    _add_platform_argument(install)
    install.set_defaults(func=_handle_install)

    sync = subparsers.add_parser("sync", help="レビューintegrationを正本と同期します")
    _add_platform_argument(sync)
    sync.set_defaults(func=_handle_sync)

    status = subparsers.add_parser("status", help="レビューintegrationの状態を表示します")
    _add_platform_argument(status)
    status.set_defaults(func=_handle_status)

    uninstall = subparsers.add_parser("uninstall", help="本ツール管理下のレビューintegrationを削除します")
    _add_platform_argument(uninstall)
    uninstall.set_defaults(func=_handle_uninstall)
    return parser


def _add_platform_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--platform",
        choices=["copilot", "antigravity", "all"],
        default="copilot",
        help="対象platform。未指定時は既存互換のcopilot。",
    )


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


def _handle_install(args: argparse.Namespace) -> int:
    if args.platform in {"copilot", "all"}:
        _print_sync_result("Copilot Custom Agentをインストール", install_agents())
        _print_mcp_result("インストール", install_mcp_config())
    if args.platform in {"antigravity", "all"}:
        _print_antigravity_result("インストール", install_antigravity())
    return 0


def _handle_sync(args: argparse.Namespace) -> int:
    if args.platform in {"copilot", "all"}:
        _print_sync_result("Copilot Custom Agentを同期", sync_agents())
        _print_mcp_result("同期", sync_mcp_config())
    if args.platform in {"antigravity", "all"}:
        _print_antigravity_result("同期", sync_antigravity())
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    if args.platform in {"copilot", "all"}:
        statuses = status_agents()
        print("Copilot Custom Agent status:")
        for item in statuses:
            label = STATUS_LABELS.get(item.status, item.status)
            print(f"- {item.installed_name}: {label}")
        current = sum(1 for item in statuses if item.status == "current")
        print(f"Current: {current}/{len(statuses)}")

        mcp_status = status_mcp_config()
        mcp_label = STATUS_LABELS.get(mcp_status.status, mcp_status.status)
        print("Copilot MCP status:")
        print(f"- {mcp_status.server_id}: {mcp_label}")
        print(f"- config: {mcp_status.config_path}")

    if args.platform in {"antigravity", "all"}:
        antigravity = status_antigravity()
        print("Antigravity status:")
        print(f"- plugin: {STATUS_LABELS.get(antigravity.plugin_status, antigravity.plugin_status)}")
        print(f"- MCP: {STATUS_LABELS.get(antigravity.mcp_status, antigravity.mcp_status)}")
        print(f"- plugin_dir: {antigravity.plugin_dir}")
        print(f"- mcp_config: {antigravity.mcp_config_path}")
    return 0


def _handle_uninstall(args: argparse.Namespace) -> int:
    if args.platform in {"copilot", "all"}:
        mcp_result = uninstall_mcp_config()
        result = uninstall_agents()
        print("本ツール管理下のCopilot Custom Agentを削除しました。")
        print(f"Destination: {result['agents_dir']}")
        print(f"Removed: {result['total']}")
        print("本ツール管理下のCopilot MCP server設定を削除しました。")
        print(f"Server: {mcp_result['server_id']}")
        print(f"Removed: {mcp_result['removed']}")

    if args.platform in {"antigravity", "all"}:
        result = uninstall_antigravity()
        print("本ツール管理下のAntigravity integrationを削除しました。")
        print(f"Plugin removed: {result['plugin_removed']}")
        print(f"MCP removed: {result['mcp_removed']}")
    return 0


def _print_antigravity_result(action: str, result: dict[str, object]) -> None:
    print(f"Antigravity integrationを{action}しました。")
    print(f"Plugin: {result['plugin_dir']}")
    print(f"Plugin action: {result['plugin_action']}")
    print(f"MCP config: {result['mcp_config_path']}")
    print(f"MCP action: {result['mcp_action']}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except (CustomAgentInstallError, McpConfigError, AntigravityIntegrationError) as exc:
        print(f"エラー: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
