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
from .diff_collector import DiffCollectionError, collect_diff
from .quality import UnsafeCommandError, run_quality_checks
from .repository import RepositoryError, resolve_repository
from .review_engine import EngineRequest, ReviewEngineError, new_run_id, run_review_engine
from .secrets import scan_diff_for_secrets
from .storage import (
    LockHeldError,
    RootPaths,
    StorageError,
    acquire_review_lock,
    cleanup_locks,
    latest_report,
    load_latest,
    request_cancel,
    save_review_result,
)
from .review_engine import EngineResult


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
    review.add_argument("--commits", help="--target commitsで使うコミット範囲")
    review.add_argument("--file", help="--target fileで使うリポジトリ内ファイル")
    review.add_argument("--exclude", action="append", default=[], help="除外するリポジトリ相対パスprefix")
    review.add_argument("--quality-command", action="append", help="allowlist済み品質チェックコマンド")
    review.add_argument("--agent", choices=[
        "requirements",
        "correctness",
        "security",
        "testing",
        "maintainability",
        "performance",
        "operations",
        "devil_advocate",
        "final",
    ], help="単独実行するエージェント")
    review.add_argument("--no-agents", action="store_true", help="Copilotエージェントを起動せず収集結果だけ表示します")
    review.add_argument(
        "--target",
        required=True,
        choices=["base", "uncommitted", "staged", "commits", "file"],
        help="レビュー対象差分の種別",
    )
    review.set_defaults(func=handle_review)

    show_latest = subparsers.add_parser("show-latest", help="指定リポジトリの最新レビュー結果を表示します")
    show_latest.add_argument("--repo", required=True, help="レビュー対象のローカルGitリポジトリパス")
    show_latest.set_defaults(func=handle_show_latest)

    cancel = subparsers.add_parser("cancel", help="指定リポジトリの実行中レビューを停止します")
    cancel.add_argument("--repo", required=True, help="レビュー対象のローカルGitリポジトリパス")
    cancel.add_argument("--run-id", help="現在run IDと一致する場合だけcancelします")
    cancel.set_defaults(func=handle_cancel)

    rerun = subparsers.add_parser("rerun", help="指定リポジトリの前回条件で再実行します")
    rerun.add_argument("--repo", required=True, help="レビュー対象のローカルGitリポジトリパス")
    rerun.add_argument("--no-agents", action="store_true", help="Copilotエージェントを起動せず収集結果だけ表示します")
    rerun.set_defaults(func=handle_rerun)

    cleanup = subparsers.add_parser("cleanup-locks", help="指定リポジトリのruntime lockを安全に掃除します")
    cleanup.add_argument("--repo", required=True, help="レビュー対象のローカルGitリポジトリパス")
    cleanup.add_argument("--apply", action="store_true", help="dry-runではなく削除を実行します")
    cleanup.set_defaults(func=handle_cleanup_locks)

    return parser


def handle_validate_config(_args: argparse.Namespace) -> int:
    info = get_copilot_version()
    print("設定検証に成功しました。")
    print(f"Copilot CLI: {info.executable}")
    print(f"Version: {info.version}")
    return 0


def handle_review(args: argparse.Namespace) -> int:
    repository = resolve_repository(args.repo, base_branch=args.base_branch)
    diff = collect_diff(
        repository,
        target=args.target,
        commits=args.commits,
        file_path=args.file,
        excludes=args.exclude,
    )
    quality = run_quality_checks(repository.root, args.quality_command)
    print("リポジトリ検証に成功しました。")
    print(f"Repository root: {repository.root}")
    print(f"Git common dir: {repository.git_common_dir}")
    print(f"Project ID: {repository.project_id}")
    print(f"Remote: {repository.remote_url or '(none)'}")
    print(f"Current branch: {repository.current_branch}")
    print(f"HEAD SHA: {repository.head_sha}")
    print(f"Base branch: {repository.base_branch}")
    print(f"Target: {args.target}")
    print(f"Changed files: {diff.changed_file_count}")
    print(f"Diff lines: {diff.diff_line_count}")
    print(f"Truncated: {diff.truncated}")
    print(f"Requirements context: {', '.join(diff.requirements_context) or '(none)'}")
    secret_scan = scan_diff_for_secrets(diff)
    print(f"Secret findings: {len(secret_scan.findings)}")
    print(f"Secret blocked: {secret_scan.blocked}")
    print("Quality checks:")
    for result in quality:
        print(f"- {result.name or '(none)'}: {result.status}")
    paths = RootPaths.from_engine_root()
    run_id = new_run_id()
    with acquire_review_lock(paths, repository, run_id):
        if args.no_agents:
            print("エージェント実行は--no-agentsによりスキップしました。Copilot CLIは呼び出していません。")
            engine_result = EngineResult(
                run_id=run_id,
                provider="github-copilot-cli",
                agent_states={},
                agent_results=[],
                final_decision="INCONCLUSIVE",
                max_concurrent_copilot_processes=0,
            )
        else:
            engine_result = run_review_engine(
                EngineRequest(
                    repository=repository,
                    diff=diff,
                    quality_checks=quality,
                    target=args.target,
                    run_id=run_id,
                    agent=args.agent,
                    cancel_file=paths.runtime_root / repository.project_id / "cancel.json",
                )
            )
        print(f"Final decision: {engine_result.final_decision}")
        print(f"Max concurrent Copilot processes: {engine_result.max_concurrent_copilot_processes}")
        save_review_result(
            paths,
            repository,
            run_id=engine_result.run_id,
            target=args.target,
            request={"target": args.target, "agent": args.agent, "no_agents": args.no_agents},
            diff=diff,
            quality_checks=quality,
            engine_result=engine_result,
            copilot_version=None,
        )
    print(f"Report: {paths.output_root / repository.project_id / 'latest' / 'report.md'}")
    return 0


def handle_show_latest(args: argparse.Namespace) -> int:
    repository = resolve_repository(args.repo)
    print(latest_report(RootPaths.from_engine_root(), repository.project_id))
    return 0


def handle_cancel(args: argparse.Namespace) -> int:
    repository = resolve_repository(args.repo)
    path = request_cancel(RootPaths.from_engine_root(), repository.project_id, args.run_id)
    print(f"キャンセル要求を作成しました: {path}")
    return 0


def handle_rerun(args: argparse.Namespace) -> int:
    repository = resolve_repository(args.repo)
    latest = load_latest(RootPaths.from_engine_root(), repository.project_id)
    target = latest.get("request", {}).get("target") or latest.get("target") or "base"
    review_args = argparse.Namespace(
        repo=args.repo,
        base_branch=latest.get("base_branch"),
        target=target,
        commits=None,
        file=None,
        exclude=[],
        quality_command=None,
        agent=latest.get("request", {}).get("agent"),
        no_agents=args.no_agents,
    )
    return handle_review(review_args)


def handle_cleanup_locks(args: argparse.Namespace) -> int:
    repository = resolve_repository(args.repo)
    result = cleanup_locks(RootPaths.from_engine_root(), repository.project_id, dry_run=not args.apply)
    print(result)
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
    except (DiffCollectionError, UnsafeCommandError) as exc:
        print(str(exc), file=sys.stderr)
        return 6
    except ReviewEngineError as exc:
        print(str(exc), file=sys.stderr)
        return 7
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 8
    except StorageError as exc:
        print(str(exc), file=sys.stderr)
        return 9
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
