from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from .repository import RepositoryContext, RepositoryError, resolve_repository


AGENT_LABELS = {
    "all": "9エージェントすべて",
    "security": "security",
}


@dataclass(frozen=True)
class ReviewPreview:
    repository: RepositoryContext
    target: str
    agents: str
    changed_file_count: int
    diff_line_count: int
    truncated: bool
    has_uncommitted: bool
    has_staged: bool
    quality_checks: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VS Code launch helper for ai-review.")
    parser.add_argument("--target", default="base", choices=["base", "uncommitted", "staged", "commits", "file"])
    parser.add_argument("--agents", default="all", choices=sorted(AGENT_LABELS))
    parser.add_argument("--base-branch")
    parser.add_argument("--action", default="review", choices=["review", "rerun", "show-latest", "cancel", "validate-config"])
    return parser


def collect_preview(repo_path: str, *, target: str, agents: str, base_branch: str | None = None) -> ReviewPreview:
    repository = resolve_repository(repo_path, base_branch=base_branch)
    changed_files, diff_lines, truncated = _diff_size(repository.root, repository.base_branch, target)
    has_uncommitted = bool(_optional_git(repository.root, "status", "--porcelain"))
    has_staged = bool(_optional_git(repository.root, "diff", "--cached", "--name-only"))
    quality_checks = _detect_quality_checks(repository.root)
    return ReviewPreview(
        repository=repository,
        target=target,
        agents=AGENT_LABELS[agents],
        changed_file_count=changed_files,
        diff_line_count=diff_lines,
        truncated=truncated,
        has_uncommitted=has_uncommitted,
        has_staged=has_staged,
        quality_checks=quality_checks,
    )


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "validate-config":
        return _run_ai_review(["validate-config"])

    if args.action in {"show-latest", "cancel", "rerun"}:
        repo = _select_repository()
        if repo is None:
            return 0
        command = "review" if args.action == "rerun" else args.action
        extra = ["--target", args.target] if command == "review" else []
        return _run_ai_review([command, "--repo", repo, *extra])

    while True:
        repo = _select_repository()
        if repo is None:
            print("対象リポジトリ選択がキャンセルされました。")
            return 0
        try:
            preview = collect_preview(repo, target=args.target, agents=args.agents, base_branch=args.base_branch)
        except RepositoryError as exc:
            _show_error("Gitリポジトリではありません", str(exc))
            continue

        decision = _confirm_execution(preview)
        if decision == "run":
            return _run_ai_review(["review", "--repo", str(preview.repository.root), "--target", args.target])
        if decision == "cancel":
            print("レビュー実行がキャンセルされました。")
            return 0


def _select_repository() -> str | None:
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(parent=root, title="レビュー対象のGitリポジトリを選択")
        root.destroy()
    except tk.TclError:
        print(
            "フォルダ選択UIを開けません。headless環境では "
            "`python -m ai_review review --repo <path> --target base` を実行してください。",
            file=sys.stderr,
        )
        return None
    return selected or None


def _confirm_execution(preview: ReviewPreview) -> str:
    message = "\n".join(
        [
            f"対象リポジトリ: {preview.repository.root}",
            f"project ID: {preview.repository.project_id}",
            f"現在ブランチ: {preview.repository.current_branch}",
            f"基準ブランチ: {preview.repository.base_branch}",
            f"レビュー種別: {preview.target}",
            f"実行エージェント: {preview.agents}",
            f"変更ファイル数: {preview.changed_file_count}",
            f"差分行数: {preview.diff_line_count}",
            f"切り捨て予定: {'あり' if preview.truncated else 'なし'}",
            f"未コミット差分を含むか: {'はい' if preview.has_uncommitted else 'いいえ'}",
            f"ステージ済み差分を含むか: {'はい' if preview.has_staged else 'いいえ'}",
            f"品質チェック検出結果: {preview.quality_checks}",
            "",
            "実行しますか？",
        ]
    )
    answer = messagebox.askyesnocancel(title="Copilotレビュー実行確認", message=message)
    if answer is True:
        return "run"
    if answer is False:
        return "retry"
    return "cancel"


def _show_error(title: str, message: str) -> None:
    try:
        messagebox.showerror(title=title, message=message)
    except tk.TclError:
        print(f"{title}: {message}", file=sys.stderr)


def _run_ai_review(args: list[str]) -> int:
    command = [sys.executable, "-m", "ai_review", *args]
    completed = subprocess.run(command, check=False, shell=False)
    return completed.returncode


def _diff_size(root: Path, base_branch: str, target: str) -> tuple[int, int, bool]:
    if target == "staged":
        args = ("diff", "--cached", "--numstat")
    elif target == "uncommitted":
        args = ("diff", "--numstat")
    else:
        args = ("diff", "--numstat", f"{base_branch}...HEAD")
    output = _optional_git(root, *args) or ""
    files = 0
    lines = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        for value in parts[:2]:
            if value.isdigit():
                lines += int(value)
    if target == "uncommitted":
        for path in (_optional_git(root, "ls-files", "--others", "--exclude-standard") or "").splitlines():
            candidate = (root / path).resolve()
            if candidate.is_file():
                files += 1
                try:
                    lines += len(candidate.read_text(encoding="utf-8").splitlines())
                except UnicodeDecodeError:
                    pass
    return files, lines, False


def _detect_quality_checks(root: Path) -> str:
    if (root / "pyproject.toml").exists():
        return "pyproject.toml"
    if (root / "package.json").exists():
        return "package.json"
    if (root / ".github" / "workflows").exists():
        return "GitHub Actions workflow"
    return "未定義のためskip"


def _optional_git(root: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace").strip()


if __name__ == "__main__":
    raise SystemExit(run())
