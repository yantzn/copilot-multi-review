from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from .review_engine import EngineResult
from .repository import RepositoryContext


class StorageError(RuntimeError):
    pass


class LockHeldError(StorageError):
    pass


@dataclass(frozen=True)
class RootPaths:
    engine_root: Path
    output_root: Path
    runtime_root: Path

    @classmethod
    def from_engine_root(cls, engine_root: Path | None = None) -> "RootPaths":
        root = (engine_root or Path.cwd()).resolve()
        return cls(engine_root=root, output_root=root / "reports", runtime_root=root / "runtime")


@dataclass(frozen=True)
class ReviewLock:
    project_id: str
    run_id: str
    owner: str
    generation: str
    path: Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def acquire_review_lock(paths: RootPaths, repository: RepositoryContext, run_id: str):
    lock = _acquire_lock(paths, repository, run_id)
    try:
        yield lock
    finally:
        release_review_lock(lock)


def _acquire_lock(paths: RootPaths, repository: RepositoryContext, run_id: str) -> ReviewLock:
    runtime_dir = paths.runtime_root / repository.project_id
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_dir / "review.lock"
    owner = f"{os.getpid()}@{os.environ.get('COMPUTERNAME') or os.environ.get('HOSTNAME') or 'localhost'}"
    generation = str(uuid.uuid4())
    payload = {
        "project_id": repository.project_id,
        "run_id": run_id,
        "status": "running",
        "current_agent": None,
        "current_agent_index": 0,
        "total_agents": 0,
        "completed_agents": [],
        "pending_agents": [],
        "owner": owner,
        "generation": generation,
        "git_common_dir": str(repository.git_common_dir),
        "started_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise LockHeldError(f"同一project IDのレビューが実行中です: {repository.project_id}") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    running_path = runtime_dir / "running.json"
    running_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return ReviewLock(repository.project_id, run_id, owner, generation, lock_path)


def update_running_status(lock: ReviewLock, **updates) -> None:
    running_path = lock.path.parent / "running.json"
    if not running_path.exists():
        return
    try:
        payload = json.loads(running_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if payload.get("owner") != lock.owner or payload.get("generation") != lock.generation:
        return
    payload.update(updates)
    payload["updated_at"] = now_iso()
    running_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def release_review_lock(lock: ReviewLock) -> None:
    if not lock.path.exists():
        return
    try:
        payload = json.loads(lock.path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if payload.get("owner") != lock.owner or payload.get("generation") != lock.generation:
        return
    lock.path.unlink()
    running_path = lock.path.parent / "running.json"
    if running_path.exists():
        try:
            running = json.loads(running_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if running.get("owner") == lock.owner and running.get("generation") == lock.generation:
            running_path.unlink()


def request_cancel(paths: RootPaths, project_id: str, run_id: str | None = None) -> Path:
    runtime_dir = paths.runtime_root / project_id
    running_path = runtime_dir / "running.json"
    if not running_path.exists():
        raise StorageError(f"実行中レビューがありません: {project_id}")
    running = json.loads(running_path.read_text(encoding="utf-8"))
    active_run_id = running["run_id"]
    if run_id and run_id != active_run_id:
        raise StorageError("指定run_idは現在実行中のrun_idと一致しません。")
    cancel_path = runtime_dir / "cancel.json"
    cancel_path.write_text(
        json.dumps({"project_id": project_id, "run_id": active_run_id, "requested_at": now_iso()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cancel_path


def load_running_statuses(paths: RootPaths, project_id: str | None = None) -> list[dict]:
    roots = [paths.runtime_root / project_id] if project_id else [path for path in paths.runtime_root.glob("*") if path.is_dir()]
    statuses = []
    for root in roots:
        running_path = root / "running.json"
        if not running_path.exists():
            continue
        try:
            statuses.append(json.loads(running_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            statuses.append({"project_id": root.name, "status": "unreadable"})
    return statuses


def format_running_status(status: dict) -> str:
    started_at = status.get("started_at") or status.get("created_at")
    elapsed = _format_elapsed(started_at)
    completed = status.get("completed_agents") or []
    pending = status.get("pending_agents") or []
    lines = [
        f"Project ID: {status.get('project_id', '(unknown)')}",
        f"Run ID: {status.get('run_id', '(unknown)')}",
        f"モード: {status.get('mode', 'diff_review')}",
        f"profile: {status.get('profile', '-')}",
        f"状態: {str(status.get('status', 'unknown')).upper()}",
        f"全体進捗: {len(status.get('completed_batches') or [])} / {status.get('total_batches', 0)} バッチ",
        f"現在バッチ: {status.get('current_batch_id') or 'なし'} {status.get('current_batch_name') or ''}".rstrip(),
        f"現在のエージェント: {status.get('current_agent') or 'なし'}",
        f"進捗: {status.get('current_agent_index', 0)} / {status.get('total_agents', 0)}",
        f"バッチ内進捗: {status.get('current_agent_index', 0)} / {status.get('current_batch_total_agents', status.get('total_agents', 0))}",
        "完了:",
        *(f"- {agent}" for agent in completed),
        *([] if completed else ["- なし"]),
        "完了バッチ:",
        *(f"- {batch}" for batch in (status.get("completed_batches") or [])),
        *([] if status.get("completed_batches") else ["- なし"]),
        "失敗バッチ:",
        *(f"- {batch}" for batch in (status.get("failed_batches") or [])),
        *([] if status.get("failed_batches") else ["- なし"]),
        "BLOCKEDバッチ:",
        *(f"- {batch}" for batch in (status.get("blocked_batches") or [])),
        *([] if status.get("blocked_batches") else ["- なし"]),
        "キャンセルバッチ:",
        *(f"- {batch}" for batch in (status.get("cancelled_batches") or [])),
        *([] if status.get("cancelled_batches") else ["- なし"]),
        "スキップバッチ:",
        *(f"- {batch}" for batch in (status.get("skipped_batches") or [])),
        *([] if status.get("skipped_batches") else ["- なし"]),
        "待機:",
        *(f"- {agent}" for agent in pending),
        *([] if pending else ["- なし"]),
        "待機バッチ:",
        *(f"- {batch}" for batch in (status.get("pending_batches") or [])),
        *([] if status.get("pending_batches") else ["- なし"]),
        f"開始日時: {started_at or '(unknown)'}",
        f"更新日時: {status.get('updated_at') or '(unknown)'}",
        f"経過時間: {elapsed}",
        f"キャンセル要求: {status.get('cancel_requested_at') or 'なし'}",
    ]
    return "\n".join(lines)


def save_review_result(
    paths: RootPaths,
    repository: RepositoryContext,
    *,
    run_id: str,
    target: str,
    request: dict,
    diff,
    quality_checks,
    engine_result: EngineResult,
    copilot_version: str | None,
) -> Path:
    project_dir = paths.output_root / repository.project_id
    history_dir = project_dir / "history" / run_id
    latest_dir = project_dir / "latest"
    agents_dir = history_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    run_json = {
        "project_id": repository.project_id,
        "repository_path": str(repository.root),
        "repository_remote": repository.remote_url,
        "current_branch": repository.current_branch,
        "base_branch": repository.base_branch,
        "head_sha": repository.head_sha,
        "base_sha": None,
        "working_tree_dirty": diff.changed_file_count > 0,
        "target": target,
        "request": request,
        "changed_file_count": diff.changed_file_count,
        "diff_line_count": diff.diff_line_count,
        "truncated": diff.truncated,
        "quality_checks": [asdict(item) for item in quality_checks],
        "agent_states": engine_result.agent_states,
        "copilot_cli_version": copilot_version,
        "run_id": run_id,
        "started_at": now_iso(),
        "finished_at": now_iso(),
    }
    final_json = {
        "run_id": run_id,
        "project_id": repository.project_id,
        "decision": engine_result.final_decision,
        "max_concurrent_copilot_processes": engine_result.max_concurrent_copilot_processes,
    }
    (history_dir / "run.json").write_text(json.dumps(run_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (history_dir / "final.json").write_text(json.dumps(final_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (history_dir / "report.md").write_text(_render_report(run_json, final_json), encoding="utf-8")
    for result in engine_result.agent_results:
        (agents_dir / f"{result.agent}.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(history_dir, latest_dir)
    return history_dir


def save_repository_audit_result(paths: RootPaths, repository: RepositoryContext, analysis, audit_result, *, request: dict) -> Path:
    project_dir = paths.output_root / repository.project_id
    history_dir = project_dir / "history" / audit_result.run_id
    latest_dir = project_dir / "latest"
    batches_dir = history_dir / "batches"
    agents_dir = history_dir / "agents"
    batches_dir.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)

    repository_summary = {
        "project_id": repository.project_id,
        "repository_path": str(repository.root),
        "repository_remote": repository.remote_url,
        "head_sha": repository.head_sha,
        "current_branch": repository.current_branch,
        "profile": audit_result.profile,
        "target_file_count": len(analysis.target_files),
        "reviewable_file_count": sum(1 for item in audit_result.coverage if item.status not in {"excluded", "blocked", "skipped"}),
        "reviewable_segment_count": sum(len(item.segments) for item in audit_result.coverage if item.status not in {"excluded", "blocked", "skipped"}),
        "no_reviewable_files": audit_result.no_reviewable_files,
        "reviewed_file_count": sum(1 for item in audit_result.coverage if item.status == "reviewed"),
        "excluded_file_count": sum(1 for item in audit_result.coverage if item.status == "excluded"),
        "skipped_file_count": sum(1 for item in audit_result.coverage if item.status == "skipped"),
        "failed_file_count": sum(1 for item in audit_result.coverage if item.status == "failed"),
        "blocked_file_count": sum(1 for item in audit_result.coverage if item.status == "blocked"),
        "cancelled_file_count": sum(1 for item in audit_result.coverage if item.status == "cancelled"),
        "unreviewed_file_count": sum(1 for item in audit_result.coverage if item.status == "unreviewed"),
        "total_batches": len(audit_result.batches),
        "completed_batches": len(audit_result.completed_batches),
        "failed_batches": len(audit_result.failed_batches),
        "blocked_batches": len(audit_result.blocked_batches),
        "cancelled_batches": len(audit_result.cancelled_batches),
        "skipped_batches": len(audit_result.skipped_batches),
        "estimated_total_lines": analysis.estimated_total_lines,
        "copilot_call_count": audit_result.copilot_call_count,
        "elapsed_seconds": audit_result.elapsed_seconds,
        "execution_mode": audit_result.execution_mode,
        "review_completed": audit_result.review_completed,
    }
    run_json = {
        "project_id": repository.project_id,
        "repository_path": str(repository.root),
        "repository_remote": repository.remote_url,
        "current_branch": repository.current_branch,
        "base_branch": repository.base_branch,
        "head_sha": repository.head_sha,
        "target": "repository",
        "mode": "repository_audit",
        "profile": audit_result.profile,
        "request": request,
        "execution_mode": audit_result.execution_mode,
        "review_completed": audit_result.review_completed,
        "agent_states": _audit_agent_states(audit_result),
        "run_id": audit_result.run_id,
        "started_at": now_iso(),
        "finished_at": now_iso(),
    }
    final_json = {
        "run_id": audit_result.run_id,
        "project_id": repository.project_id,
        "decision": audit_result.final_decision,
        "max_concurrent_copilot_processes": audit_result.max_active_calls,
        "blocked_batches": audit_result.blocked_batches,
        "blocked_phase": audit_result.blocked_phase,
        "blocked_agent": audit_result.blocked_agent,
        "blocked_source": audit_result.blocked_source,
        "failed_batches": audit_result.failed_batches,
        "cancelled_batches": audit_result.cancelled_batches,
        "skipped_batches": audit_result.skipped_batches,
        "completed_batches": audit_result.completed_batches,
        "unreviewed_files": audit_result.unreviewed_files,
        "execution_mode": audit_result.execution_mode,
        "review_completed": audit_result.review_completed,
        "no_reviewable_files": audit_result.no_reviewable_files,
        "errors": [asdict(item) for item in audit_result.errors],
    }
    coverage_json = [asdict(item) for item in audit_result.coverage]
    (history_dir / "run.json").write_text(json.dumps(run_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (history_dir / "final.json").write_text(json.dumps(final_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (history_dir / "repository-summary.json").write_text(json.dumps(repository_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (history_dir / "coverage.json").write_text(json.dumps(coverage_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (history_dir / "report.md").write_text(_render_audit_report(repository_summary, final_json), encoding="utf-8")
    for batch in audit_result.batches:
        (batches_dir / f"{batch.batch_id}.json").write_text(json.dumps(asdict(batch), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for batch_id, results in audit_result.batch_results.items():
        for result in results:
            (agents_dir / f"{batch_id}-{result.agent}.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    for result in audit_result.cross_results:
        (agents_dir / f"cross-{result.agent}.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(history_dir, latest_dir)
    return history_dir


def _audit_agent_states(audit_result) -> dict[str, str]:
    states: dict[str, str] = {}
    for batch in audit_result.batches:
        if batch.batch_id in audit_result.completed_batches:
            states[batch.batch_id] = "completed"
        elif batch.batch_id in audit_result.failed_batches:
            states[batch.batch_id] = "failed"
        elif batch.batch_id in audit_result.blocked_batches:
            states[batch.batch_id] = "blocked"
        elif batch.batch_id in audit_result.cancelled_batches:
            states[batch.batch_id] = "cancelled"
        elif batch.batch_id in audit_result.skipped_batches:
            states[batch.batch_id] = "skipped"
        else:
            states[batch.batch_id] = "pending"
    return states


def load_latest(paths: RootPaths, project_id: str) -> dict:
    run_path = paths.output_root / project_id / "latest" / "run.json"
    if not run_path.exists():
        raise StorageError(f"最新結果がありません: {project_id}")
    return json.loads(run_path.read_text(encoding="utf-8"))


def latest_report(paths: RootPaths, project_id: str) -> str:
    report_path = paths.output_root / project_id / "latest" / "report.md"
    if not report_path.exists():
        raise StorageError(f"最新レポートがありません: {project_id}")
    return report_path.read_text(encoding="utf-8")


def cleanup_locks(paths: RootPaths, project_id: str, *, dry_run: bool = True) -> dict:
    runtime_dir = paths.runtime_root / project_id
    if not runtime_dir.exists():
        return {"status": "nothing_to_delete", "dry_run": dry_run, "deleted": []}
    targets = [runtime_dir / "review.lock", runtime_dir / "cancel.json", runtime_dir / "running.json"]
    existing = [path for path in targets if path.exists()]
    if dry_run:
        return {"status": "refused" if existing else "nothing_to_delete", "dry_run": True, "deleted": []}
    deleted = []
    for path in existing:
        path.unlink()
        deleted.append(path.name)
    return {"status": "deleted" if deleted else "nothing_to_delete", "dry_run": False, "deleted": deleted}


def _format_elapsed(started_at: str | None) -> str:
    if not started_at:
        return "00:00:00"
    try:
        start = datetime.fromisoformat(started_at)
    except ValueError:
        return "00:00:00"
    seconds = max(0, int(time.time() - start.timestamp()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def _render_report(run_json: dict, final_json: dict) -> str:
    return "\n".join(
        [
            f"# Review Report: {run_json['project_id']}",
            "",
            f"- run_id: {run_json['run_id']}",
            f"- target: {run_json['target']}",
            f"- decision: {final_json['decision']}",
            f"- changed files: {run_json['changed_file_count']}",
            f"- diff lines: {run_json['diff_line_count']}",
            f"- truncated: {run_json['truncated']}",
            "",
        ]
    )


def _render_audit_report(summary: dict, final_json: dict) -> str:
    return "\n".join(
        [
            f"# Repository Audit: {summary['project_id']}",
            "",
            f"- run_id: {final_json['run_id']}",
            f"- profile: {summary['profile']}",
            f"- decision: {final_json['decision']}",
            f"- target files: {summary['target_file_count']}",
            f"- reviewable files: {summary.get('reviewable_file_count', 0)}",
            f"- reviewable segments: {summary.get('reviewable_segment_count', 0)}",
            f"- reviewed files: {summary['reviewed_file_count']}",
            f"- excluded files: {summary['excluded_file_count']}",
            f"- total batches: {summary['total_batches']}",
            f"- failed batches: {summary['failed_batches']}",
            f"- blocked batches: {summary['blocked_batches']}",
            f"- copilot calls: {summary['copilot_call_count']}",
            *(
                ["", "レビュー可能な対象ファイルがありません。"]
                if summary.get("no_reviewable_files")
                else []
            ),
            "",
        ]
    )
