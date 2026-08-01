from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

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
from .repository_audit import AuditError, AuditOptions, analyze_repository, run_repository_audit
from .review_engine import CopilotClient, EngineRequest, ReviewEngineError, new_run_id, run_review_engine
from .secrets import scan_diff_for_secrets
from .storage import (
    LockHeldError,
    RootPaths,
    StorageError,
    acquire_review_lock,
    cleanup_locks,
    latest_report,
    format_running_status,
    load_running_statuses,
    load_latest,
    request_cancel,
    save_repository_audit_result,
    save_review_result,
    update_running_status,
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
        choices=["base", "uncommitted", "staged", "commits", "file", "repository"],
        help="レビュー対象差分の種別",
    )
    review.set_defaults(func=handle_review)

    audit = subparsers.add_parser("audit", help="外部ローカルGitリポジトリ全体を分割監査します")
    _add_audit_arguments(audit)
    audit.set_defaults(func=handle_audit)

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

    status = subparsers.add_parser("status", help="実行中レビューの状況を表示します")
    status.add_argument("--repo", help="レビュー対象のローカルGitリポジトリパス")
    status.add_argument("--watch", action="store_true", help="1秒ごとに実行状況を再表示します")
    status.add_argument("--interval", type=float, default=1.0, help="--watch時の更新間隔秒")
    status.set_defaults(func=handle_status)

    return parser


def _add_audit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, help="監査対象のローカルGitリポジトリパス")
    parser.add_argument("--profile", choices=["quick", "standard", "deep"], default=None)
    parser.add_argument("--include-untracked", action="store_true")
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--max-total-lines", type=int, default=None)
    parser.add_argument("--max-copilot-calls", type=int, default=None)
    parser.add_argument("--max-files-per-batch", type=int, default=None)
    parser.add_argument("--max-lines-per-batch", type=int, default=None)
    parser.add_argument("--max-chars-per-batch", type=int, default=None)
    parser.add_argument("--max-file-bytes", type=int, default=None)
    parser.add_argument("--rerun", action="store_true", help="前回条件で完全再実行します")
    parser.add_argument("--no-agents", action="store_true", help="Copilotを呼ばず事前分析とレポート保存だけ行います")


def handle_validate_config(_args: argparse.Namespace) -> int:
    info = get_copilot_version()
    print("設定検証に成功しました。")
    print(f"Copilot CLI: {info.executable}")
    print(f"Version: {info.version}")
    return 0


def handle_review(args: argparse.Namespace) -> int:
    if args.target == "repository":
        audit_args = argparse.Namespace(
            repo=args.repo,
            profile=None,
            include_untracked=False,
            max_batches=None,
            max_files=None,
            max_total_lines=None,
            max_copilot_calls=None,
            max_files_per_batch=None,
            max_lines_per_batch=None,
            max_chars_per_batch=None,
            max_file_bytes=None,
            rerun=False,
            no_agents=getattr(args, "no_agents", False),
        )
        return handle_audit(audit_args)
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
    with acquire_review_lock(paths, repository, run_id) as lock:
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
                    progress_callback=lambda progress: _handle_progress(lock, progress),
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


def handle_audit(args: argparse.Namespace) -> int:
    repository = resolve_repository(args.repo)
    if args.rerun:
        _restore_audit_rerun_options(args, repository.project_id)
    options = _audit_options_from_args(args)
    paths = RootPaths.from_engine_root()
    run_id = new_run_id()
    analysis, batches = analyze_repository(repository, options)
    print("[ai-review] リポジトリ全体監査を開始")
    print(f"[ai-review] profile: {options.profile}")
    print(f"[ai-review] 対象ファイル: {len(analysis.target_files)}")
    print(f"[ai-review] 除外ファイル: {sum(1 for item in analysis.coverage if item.status == 'excluded')}")
    print(f"[ai-review] 推定総行数: {analysis.estimated_total_lines}")
    print(f"[ai-review] バッチ数: {len(batches)}")
    print(f"[ai-review] 想定Copilot呼び出し数: {analysis.expected_copilot_calls}")
    with acquire_review_lock(paths, repository, run_id) as lock:
        update_running_status(
            lock,
            mode="repository_audit",
            profile=options.profile,
            total_batches=len(batches),
            pending_batches=[batch.batch_id for batch in batches],
        )
        analysis, result = run_repository_audit(
            repository,
            options,
            run_id=run_id,
            client=CopilotClient(),
            progress_callback=lambda progress: _handle_audit_progress(lock, progress),
            cancel_file=paths.runtime_root / repository.project_id / "cancel.json",
        )
        save_repository_audit_result(
            paths,
            repository,
            analysis,
            result,
            request={
                "mode": "repository_audit",
                "profile": options.profile,
                "include_untracked": options.include_untracked,
                "max_batches": options.max_batches,
                "max_files": options.max_files,
                "max_total_lines": options.max_total_lines,
                "max_copilot_calls": options.max_copilot_calls,
                "max_files_per_batch": options.max_files_per_batch,
                "max_lines_per_batch": options.max_lines_per_batch,
                "max_chars_per_batch": options.max_chars_per_batch,
                "max_file_bytes": options.max_file_bytes,
            },
        )
    print(f"Final decision: {result.final_decision}")
    if result.execution_mode == "analysis_only":
        print("Execution mode: ANALYSIS_ONLY")
        print("Review decision: NOT_RUN")
    print(f"Max concurrent Copilot processes: {result.max_active_calls}")
    print(f"Report: {paths.output_root / repository.project_id / 'latest' / 'report.md'}")
    return _audit_exit_code(result)


def _restore_audit_rerun_options(args: argparse.Namespace, project_id: str) -> None:
    latest = load_latest(RootPaths.from_engine_root(), project_id)
    request = latest.get("request", {})
    if request.get("mode") != "repository_audit":
        raise AuditError("前回のrepository audit条件が見つかりません。通常のauditを実行してください。")
    for key in (
        "profile",
        "include_untracked",
        "max_batches",
        "max_files",
        "max_total_lines",
        "max_copilot_calls",
        "max_files_per_batch",
        "max_lines_per_batch",
        "max_chars_per_batch",
        "max_file_bytes",
    ):
        if key in request:
            setattr(args, key, request[key])


def _audit_options_from_args(args: argparse.Namespace) -> AuditOptions:
    defaults = _repository_audit_config()

    def value(name: str):
        arg_value = getattr(args, name)
        if arg_value is not None:
            return arg_value
        return defaults.get(name, AuditOptions.__dataclass_fields__[name].default)

    return AuditOptions(
        profile=value("profile"),
        include_untracked=args.include_untracked,
        max_batches=value("max_batches"),
        max_files=value("max_files"),
        max_total_lines=value("max_total_lines"),
        max_copilot_calls=value("max_copilot_calls"),
        max_files_per_batch=value("max_files_per_batch"),
        max_lines_per_batch=value("max_lines_per_batch"),
        max_chars_per_batch=value("max_chars_per_batch"),
        max_file_bytes=value("max_file_bytes"),
        rerun=args.rerun,
        no_agents=args.no_agents,
    )


def _repository_audit_config() -> dict:
    path = Path(__file__).resolve().parent.parent / "config" / "common.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("repository_audit", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _audit_exit_code(result) -> int:
    if result.execution_mode == "analysis_only":
        return 0
    if result.cancelled_batches:
        return 4
    return {
        "APPROVE": 0,
        "APPROVE_WITH_NOTES": 0,
        "CHANGES_REQUIRED": 1,
        "BLOCKED": 2,
        "INCONCLUSIVE": 3,
    }.get(result.final_decision, 3)


def handle_status(args: argparse.Namespace) -> int:
    paths = RootPaths.from_engine_root()
    last_output = ""

    def show_once(*, force: bool = False) -> tuple[bool, str]:
        project_id = resolve_repository(args.repo).project_id if args.repo else None
        statuses = load_running_statuses(paths, project_id)
        if not statuses:
            output = "実行中レビューはありません。"
            if force:
                print(output)
            return True, output
        parts: list[str] = []
        for index, status in enumerate(statuses):
            if index:
                parts.append("")
            parts.append(format_running_status(status))
        output = "\n".join(parts)
        if force:
            print(output)
        terminal = {"completed", "failed", "blocked", "cancelled"}
        return all(str(status.get("status", "")).lower() in terminal for status in statuses), output

    if not args.watch:
        show_once(force=True)
        return 0

    interval = max(1.0, args.interval)
    try:
        while True:
            nonlocal_last_output = last_output
            done, output = show_once()
            if output != nonlocal_last_output:
                print(output)
                last_output = output
            if done:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        print("status表示を終了しました。")
        return 0


def _handle_progress(lock, progress: dict) -> None:
    current = progress.get("current_agent")
    index = progress.get("current_agent_index", 0)
    total = progress.get("total_agents", 0)
    completed = progress.get("completed_agents") or []
    pending = progress.get("pending_agents") or []
    if current:
        print(f"[ai-review] [{index}/{total}] {current} を実行中...")
    else:
        print(f"[ai-review] 進捗: {index}/{total}")
    print(f"[ai-review] 完了: {', '.join(completed) or 'なし'}")
    print(f"[ai-review] 待機: {', '.join(pending) or 'なし'}")
    update_running_status(lock, **progress)


def _handle_audit_progress(lock, progress: dict) -> None:
    if progress.get("current_batch_id"):
        print(f"[ai-review] バッチ [{progress.get('current_batch_index')}/{progress.get('total_batches')}] {progress.get('current_batch_name')}")
    if progress.get("current_agent"):
        print(f"[ai-review] エージェント [{progress.get('current_agent_index')}/{progress.get('current_batch_total_agents')}] {progress.get('current_agent')}")
    update_running_status(lock, **progress)


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
    except AuditError as exc:
        print(str(exc), file=sys.stderr)
        return 10
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
