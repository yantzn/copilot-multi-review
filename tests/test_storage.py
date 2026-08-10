from __future__ import annotations

from pathlib import Path
import json
import subprocess

import pytest

from ai_review.diff_collector import collect_diff
from ai_review.quality import QualityCheckResult
from ai_review.repository import resolve_repository
from ai_review.review_engine import EngineResult
from ai_review.storage import (
    LockHeldError,
    RootPaths,
    acquire_review_lock,
    cleanup_locks,
    latest_report,
    request_cancel,
    save_review_result,
)


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.decode("utf-8").strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("# sample\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")
    return path


def test_save_review_result_uses_engine_reports_not_target_repo(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    target = init_repo(tmp_path / "target repo")
    paths = RootPaths.from_engine_root(engine)
    repository = resolve_repository(str(target))
    diff = collect_diff(repository, target="base")
    result = EngineResult("run-1", "github-copilot-cli", {}, [], "INCONCLUSIVE", 0)

    save_review_result(
        paths,
        repository,
        run_id="run-1",
        target="base",
        request={"target": "base"},
        diff=diff,
        quality_checks=[QualityCheckResult(name="quality", command=[], status="skipped")],
        engine_result=result,
        copilot_version=None,
    )

    assert (paths.output_root / repository.project_id / "latest" / "run.json").exists()
    assert (paths.output_root / repository.project_id / "history" / "run-1" / "run.json").exists()
    run_json = json.loads((paths.output_root / repository.project_id / "latest" / "run.json").read_text(encoding="utf-8"))
    final_json = json.loads(
        (paths.output_root / repository.project_id / "latest" / "final.json").read_text(encoding="utf-8")
    )
    assert run_json["execution_mode"] == "subagent"
    assert final_json["execution_mode"] == "subagent"
    assert "- execution_mode: subagent" in latest_report(paths, repository.project_id)
    assert not (target / "reports").exists()
    assert not (target / "runtime").exists()


def test_lock_rejects_double_start_and_releases_own_generation(tmp_path: Path) -> None:
    target = init_repo(tmp_path / "target")
    paths = RootPaths.from_engine_root(tmp_path / "engine")
    repository = resolve_repository(str(target))

    with acquire_review_lock(paths, repository, "run-1") as lock:
        with pytest.raises(LockHeldError):
            with acquire_review_lock(paths, repository, "run-2"):
                pass
        assert lock.path.exists()
        assert (lock.path.parent / "running.json").exists()

    assert not lock.path.exists()
    assert not (lock.path.parent / "running.json").exists()


def test_lock_releases_on_exception(tmp_path: Path) -> None:
    target = init_repo(tmp_path / "target")
    paths = RootPaths.from_engine_root(tmp_path / "engine")
    repository = resolve_repository(str(target))

    with pytest.raises(RuntimeError):
        with acquire_review_lock(paths, repository, "run-1") as lock:
            assert lock.path.exists()
            raise RuntimeError("boom")

    runtime_dir = paths.runtime_root / repository.project_id
    assert not (runtime_dir / "review.lock").exists()
    assert not (runtime_dir / "running.json").exists()


def test_cancel_only_current_run_id(tmp_path: Path) -> None:
    target = init_repo(tmp_path / "target")
    paths = RootPaths.from_engine_root(tmp_path / "engine")
    repository = resolve_repository(str(target))

    with acquire_review_lock(paths, repository, "run-1"):
        with pytest.raises(Exception):
            request_cancel(paths, repository.project_id, "other")
        cancel = request_cancel(paths, repository.project_id, "run-1")
        assert json.loads(cancel.read_text(encoding="utf-8"))["run_id"] == "run-1"


def test_cleanup_locks_dry_run_and_apply(tmp_path: Path) -> None:
    target = init_repo(tmp_path / "target")
    paths = RootPaths.from_engine_root(tmp_path / "engine")
    repository = resolve_repository(str(target))

    with acquire_review_lock(paths, repository, "run-1"):
        dry = cleanup_locks(paths, repository.project_id)
        assert dry["status"] == "refused"
        applied = cleanup_locks(paths, repository.project_id, dry_run=False)
        assert applied["status"] == "deleted"


def test_latest_report_is_project_separated(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    first = init_repo(tmp_path / "first")
    second = init_repo(tmp_path / "second")
    paths = RootPaths.from_engine_root(engine)
    for repo, run_id in ((first, "run-a"), (second, "run-b")):
        repository = resolve_repository(str(repo))
        save_review_result(
            paths,
            repository,
            run_id=run_id,
            target="base",
            request={"target": "base"},
            diff=collect_diff(repository, target="base"),
            quality_checks=[],
            engine_result=EngineResult(run_id, "github-copilot-cli", {}, [], "INCONCLUSIVE", 0),
            copilot_version=None,
        )

    first_context = resolve_repository(str(first))
    second_context = resolve_repository(str(second))
    assert "run-a" in latest_report(paths, first_context.project_id)
    assert "run-b" in latest_report(paths, second_context.project_id)
