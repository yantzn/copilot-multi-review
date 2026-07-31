from __future__ import annotations

from pathlib import Path
import os
import subprocess

import pytest

from ai_review.diff_collector import DiffCollectionError, collect_diff
from ai_review.repository import resolve_repository


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


def context(path: Path):
    return resolve_repository(str(path))


def test_uncommitted_includes_untracked_pseudo_diff(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "新 規.txt").write_text("hello\nworld\n", encoding="utf-8")

    diff = collect_diff(context(repo), target="uncommitted")

    assert diff.changed_file_count == 1
    assert diff.diff_line_count == 2
    assert "新 規.txt" in diff.diff_text


def test_staged_diff_uses_name_status_z(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("# changed\n", encoding="utf-8")
    git(repo, "add", "README.md")

    diff = collect_diff(context(repo), target="staged")

    assert diff.changed_files[0].status == "M"
    assert diff.diff_line_count == 2


def test_base_diff_and_rename(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    git(repo, "switch", "-c", "feature")
    git(repo, "mv", "README.md", "README renamed.md")
    git(repo, "commit", "-m", "rename")

    diff = collect_diff(context(repo), target="base")

    assert diff.changed_files[0].status == "R"
    assert diff.changed_files[0].old_path == "README.md"
    assert diff.changed_files[0].path == "README renamed.md"


def test_binary_file_is_rejected(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "image.bin").write_bytes(b"abc\0def")

    diff = collect_diff(context(repo), target="uncommitted")

    assert diff.changed_files[0].binary is True
    assert diff.changed_files[0].rejected_reason == "binary"


def test_file_target_rejects_outside_path(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")

    with pytest.raises(DiffCollectionError):
        collect_diff(context(repo), target="file", file_path=str(outside))


def test_exclude_applies_to_changed_files(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "dist").mkdir()
    (repo / "dist" / "generated.txt").write_text("x\n", encoding="utf-8")

    diff = collect_diff(context(repo), target="uncommitted")

    assert diff.changed_file_count == 0


def test_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windowsのsymlink権限に依存するためPOSIXで確認します。")
    repo = init_repo(tmp_path / "repo")
    (repo / "target.txt").write_text("x\n", encoding="utf-8")
    (repo / "link.txt").symlink_to(repo / "target.txt")

    diff = collect_diff(context(repo), target="uncommitted")

    assert diff.changed_files[0].rejected_reason == "dangerous_symlink"
