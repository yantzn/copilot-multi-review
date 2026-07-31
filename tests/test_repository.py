from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from ai_review.repository import RepositoryError, build_project_id, resolve_repository


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
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


def test_resolve_repository_supports_absolute_and_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = init_repo(tmp_path / "sample repo")
    monkeypatch.chdir(tmp_path)

    absolute = resolve_repository(str(repo))
    relative = resolve_repository("sample repo")

    assert absolute.root == repo.resolve()
    assert relative.root == repo.resolve()
    assert absolute.current_branch == "main"
    assert absolute.base_branch == "main"


def test_resolve_repository_supports_unicode_and_quotes(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "日本語 'repo'")
    context = resolve_repository(str(repo))

    assert context.root == repo.resolve()
    assert "__" in context.project_id
    assert context.project_id.rsplit("__", 1)[1]


def test_remote_project_id_uses_host_owner_repo(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "remote")
    git(repo, "remote", "add", "origin", "https://github.com/yantzn/nande-shorts-ai.git")

    context = resolve_repository(str(repo))

    assert context.remote_url == "https://github.com/yantzn/nande-shorts-ai.git"
    assert context.project_id == "github.com__yantzn__nande-shorts-ai"


def test_remote_less_project_ids_do_not_collide(tmp_path: Path) -> None:
    first = init_repo(tmp_path / "a" / "same")
    second = init_repo(tmp_path / "b" / "same")

    assert build_project_id(first, None) != build_project_id(second, None)


def test_explicit_base_branch_wins(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "base")

    context = resolve_repository(str(repo), base_branch="release")

    assert context.base_branch == "release"


def test_non_git_repository_is_rejected_before_copilot(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(RepositoryError):
        resolve_repository(str(plain))


def test_worktree_is_supported(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    worktree = tmp_path / "worktree"
    git(repo, "worktree", "add", str(worktree), "-b", "feature")

    context = resolve_repository(str(worktree))

    assert context.root == worktree.resolve()
    assert context.current_branch == "feature"
    assert context.git_common_dir.name == ".git"
