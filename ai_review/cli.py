from __future__ import annotations

import argparse
import sys

from . import __version__
from .copilot import (
    CopilotError,
    CopilotNotInstalledError,
    ensure_supported_python,
    get_copilot_version,
)
from .repository import RepositoryError, resolve_repository


class CommandNotImplementedError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-review",
        description="GitHub Copilot CLI専用のローカル・マルチエージェントレビュー基盤",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    validate = subparsers.add_parser("validate-config", help="共通設定とCopilot CLIを検証します")
    validate.set_defaults(func=handle_validate_config)

    review = subparsers.add_parser("review", help="指定したローカルGitリポジトリをレビューします")
    review.add_argument("--repo", required=True, help="レビュー対象のローカルGitリポジトリパス")
    review.add_argument("--base-branch", help="基準ブランチを明示指定します")
    review.add_argument(
        "--target",
        required=True,
        choices=["base", "uncommitted", "staged", "commits", "file"],
        help="レビュー対象差分の種別",
    )
    review.set_defaults(func=handle_review)

    show_latest = subparsers.add_parser("show-latest", help="指定リポジトリの最新レビュー結果を表示します")
    show_latest.add_argument("--repo", required=True, help="レビュー対象のローカルGitリポジトリパス")
    show_latest.set_defaults(func=handle_not_implemented)

    cancel = subparsers.add_parser("cancel", help="指定リポジトリの実行中レビューを停止します")
    cancel.add_argument("--repo", required=True, help="レビュー対象のローカルGitリポジトリパス")
    cancel.set_defaults(func=handle_not_implemented)

    return parser


def handle_validate_config(_args: argparse.Namespace) -> int:
    info = get_copilot_version()
    print("設定検証に成功しました。")
    print(f"Copilot CLI: {info.executable}")
    print(f"Version: {info.version}")
    return 0


def handle_review(args: argparse.Namespace) -> int:
    repository = resolve_repository(args.repo, base_branch=args.base_branch)
    print("リポジトリ検証に成功しました。")
    print(f"Repository root: {repository.root}")
    print(f"Git common dir: {repository.git_common_dir}")
    print(f"Project ID: {repository.project_id}")
    print(f"Remote: {repository.remote_url or '(none)'}")
    print(f"Current branch: {repository.current_branch}")
    print(f"HEAD SHA: {repository.head_sha}")
    print(f"Base branch: {repository.base_branch}")
    print(f"Target: {args.target}")
    print("レビューエンジン実行は後続Issueで実装します。Copilot CLIは呼び出していません。")
    return 0


def handle_not_implemented(args: argparse.Namespace) -> int:
    raise CommandNotImplementedError(
        f"`{args.command}`はCLI構造のみ定義済みです。後続Issueで実装します。"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        ensure_supported_python()
        parser = build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            return int(exc.code or 0)
        if not hasattr(args, "func"):
            parser.print_help()
            return 0
        return int(args.func(args))
    except CopilotNotInstalledError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except CopilotError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except CommandNotImplementedError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except RepositoryError as exc:
        print(str(exc), file=sys.stderr)
        return 5
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
