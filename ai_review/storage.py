from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
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
        "owner": owner,
        "generation": generation,
        "git_common_dir": str(repository.git_common_dir),
        "created_at": now_iso(),
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
