from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .processes import decode_output, run_command
from .repository import RepositoryContext


class DiffCollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChangedFile:
    status: str
    path: str
    old_path: str | None = None
    binary: bool = False
    rejected_reason: str | None = None


@dataclass(frozen=True)
class DiffSummary:
    target: str
    changed_files: list[ChangedFile]
    diff_text: str
    changed_file_count: int
    diff_line_count: int
    truncated: bool
    truncation_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    requirements_context: list[str] = field(default_factory=list)


DEFAULT_EXCLUDES = (
    ".git/",
    "reports/",
    "runtime/",
    ".venv/",
    "__pycache__/",
    "node_modules/",
    "dist/",
    "build/",
)


MAX_DIFF_BYTES = 200_000
MAX_FILE_BYTES = 200_000


def collect_diff(
    repository: RepositoryContext,
    *,
    target: str,
    commits: str | None = None,
    file_path: str | None = None,
    excludes: list[str] | None = None,
) -> DiffSummary:
    exclude_patterns = tuple(DEFAULT_EXCLUDES + tuple(excludes or ()))
    changed_files = _changed_files(repository, target=target, commits=commits, file_path=file_path)
    filtered = [item for item in changed_files if not _is_excluded(item.path, exclude_patterns)]
    warnings: list[str] = []
    safe_files: list[ChangedFile] = []
    for item in filtered:
        safe_files.append(_validate_changed_file(repository.root, item))
    diff_text = _diff_text(repository, target=target, commits=commits, file_path=file_path)
    if target == "uncommitted":
        diff_text += _pseudo_diff_for_untracked(repository.root, safe_files)
    truncated = len(diff_text.encode("utf-8", errors="replace")) > MAX_DIFF_BYTES
    truncation_reason = "max_diff_bytes" if truncated else None
    if truncated:
        warnings.append("差分が上限を超えたため切り捨てました。")
        diff_text = diff_text.encode("utf-8", errors="replace")[:MAX_DIFF_BYTES].decode("utf-8", errors="replace")
    diff_line_count = sum(1 for line in diff_text.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    return DiffSummary(
        target=target,
        changed_files=safe_files,
        diff_text=diff_text,
        changed_file_count=len(safe_files),
        diff_line_count=diff_line_count,
        truncated=truncated,
        truncation_reason=truncation_reason,
        warnings=warnings,
        requirements_context=_collect_requirements_context(repository.root, exclude_patterns),
    )


def _changed_files(
    repository: RepositoryContext,
    *,
    target: str,
    commits: str | None,
    file_path: str | None,
) -> list[ChangedFile]:
    if target == "staged":
        args = ["git", "diff", "--cached", "--name-status", "-z"]
    elif target == "uncommitted":
        args = ["git", "diff", "--name-status", "-z"]
    elif target == "commits":
        if not commits or ".." not in commits:
            raise DiffCollectionError("--target commitsには--commits <from>..<to>が必要です。")
        args = ["git", "diff", "--name-status", "-z", commits]
    elif target == "file":
        if not file_path:
            raise DiffCollectionError("--target fileには--fileが必要です。")
        safe = _safe_repo_relative_path(repository.root, file_path)
        args = ["git", "diff", "--name-status", "-z", "--", safe]
    else:
        args = ["git", "diff", "--name-status", "-z", f"{repository.base_branch}...HEAD"]
    completed = run_command(args, cwd=repository.root)
    if completed.returncode != 0:
        raise DiffCollectionError(decode_output(completed.stderr).strip())
    parsed = _parse_name_status(completed.stdout)
    if target == "uncommitted":
        parsed.extend(_untracked_files(repository.root))
    return parsed


def _diff_text(repository: RepositoryContext, *, target: str, commits: str | None, file_path: str | None) -> str:
    if target == "staged":
        args = ["git", "diff", "--cached", "--no-ext-diff", "--binary"]
    elif target == "uncommitted":
        args = ["git", "diff", "--no-ext-diff", "--binary"]
    elif target == "commits":
        args = ["git", "diff", "--no-ext-diff", "--binary", commits or ""]
    elif target == "file":
        safe = _safe_repo_relative_path(repository.root, file_path or "")
        args = ["git", "diff", "--no-ext-diff", "--binary", "--", safe]
    else:
        args = ["git", "diff", "--no-ext-diff", "--binary", f"{repository.base_branch}...HEAD"]
    completed = run_command(args, cwd=repository.root)
    if completed.returncode != 0:
        raise DiffCollectionError(decode_output(completed.stderr).strip())
    return decode_output(completed.stdout)


def _parse_name_status(data: bytes) -> list[ChangedFile]:
    parts = [part for part in decode_output(data).split("\0") if part]
    results: list[ChangedFile] = []
    index = 0
    while index < len(parts):
        status = parts[index]
        index += 1
        if status.startswith(("R", "C")):
            old_path = parts[index]
            new_path = parts[index + 1]
            index += 2
            results.append(ChangedFile(status=status[0], old_path=old_path, path=new_path))
        else:
            path = parts[index]
            index += 1
            results.append(ChangedFile(status=status[0], path=path))
    return results


def _untracked_files(root: Path) -> list[ChangedFile]:
    completed = run_command(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root)
    if completed.returncode != 0:
        return []
    return [ChangedFile(status="?", path=path) for path in decode_output(completed.stdout).split("\0") if path]


def _validate_changed_file(root: Path, item: ChangedFile) -> ChangedFile:
    path = _safe_repo_relative_path(root, item.path)
    absolute = (root / path).resolve()
    if item.status == "D":
        return item
    if absolute.is_symlink():
        return ChangedFile(**{**item.__dict__, "rejected_reason": "dangerous_symlink"})
    if absolute.exists() and absolute.is_file():
        size = absolute.stat().st_size
        if size > MAX_FILE_BYTES:
            return ChangedFile(**{**item.__dict__, "rejected_reason": "large_file"})
        sample = absolute.read_bytes()[:8192]
        if b"\0" in sample:
            return ChangedFile(**{**item.__dict__, "binary": True, "rejected_reason": "binary"})
    return item


def _safe_repo_relative_path(root: Path, path: str) -> str:
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise DiffCollectionError(f"リポジトリ外のパスは指定できません: {path}") from exc
    return candidate.relative_to(root.resolve()).as_posix()


def _pseudo_diff_for_untracked(root: Path, files: list[ChangedFile]) -> str:
    chunks: list[str] = []
    for item in files:
        if item.status != "?" or item.rejected_reason:
            continue
        path = _safe_repo_relative_path(root, item.path)
        absolute = (root / path).resolve()
        try:
            text = absolute.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = absolute.read_text(encoding="cp932")
            except UnicodeDecodeError:
                continue
        chunks.append(f"\ndiff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n")
        chunks.extend(f"+{line}\n" for line in text.splitlines())
    return "".join(chunks)


def _collect_requirements_context(root: Path, excludes: tuple[str, ...]) -> list[str]:
    candidates = ["README.md", "README.rst", "docs/requirements.md", "requirements.txt", "pyproject.toml", "package.json"]
    found = []
    for candidate in candidates:
        path = PurePosixPath(candidate).as_posix()
        if _is_excluded(path, excludes):
            continue
        if (root / path).exists():
            found.append(path)
    return found


def _is_excluded(path: str, excludes: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/")
    return any(normalized == pattern.rstrip("/") or normalized.startswith(pattern) for pattern in excludes)
