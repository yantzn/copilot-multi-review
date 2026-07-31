from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse


class RepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepositoryContext:
    input_path: Path
    root: Path
    git_common_dir: Path
    remote_url: str | None
    current_branch: str
    head_sha: str
    base_branch: str
    project_id: str


def resolve_repository(repo_path: str, *, base_branch: str | None = None) -> RepositoryContext:
    input_path = Path(repo_path).expanduser().resolve()
    if not input_path.exists():
        raise RepositoryError(f"指定されたパスが存在しません: {input_path}")
    if not input_path.is_dir():
        raise RepositoryError(f"指定されたパスはディレクトリではありません: {input_path}")

    root = Path(_git(input_path, "rev-parse", "--show-toplevel")).resolve()
    common_dir_raw = _git(root, "rev-parse", "--git-common-dir")
    git_common_dir = (root / common_dir_raw).resolve() if not Path(common_dir_raw).is_absolute() else Path(common_dir_raw).resolve()
    remote_url = _optional_git(root, "config", "--get", "remote.origin.url")
    current_branch = _current_branch(root)
    head_sha = _git(root, "rev-parse", "HEAD")
    detected_base = _detect_base_branch(root, base_branch)
    project_id = build_project_id(root, remote_url)

    return RepositoryContext(
        input_path=input_path,
        root=root,
        git_common_dir=git_common_dir,
        remote_url=remote_url,
        current_branch=current_branch,
        head_sha=head_sha,
        base_branch=detected_base,
        project_id=project_id,
    )


def build_project_id(root: Path, remote_url: str | None) -> str:
    if remote_url:
        parsed = _parse_remote(remote_url)
        if parsed:
            host, owner, repo = parsed
            return "__".join(_slug(part) for part in (host, owner, repo))

    normalized = str(root.resolve()).casefold().replace("\\", "/")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{_slug(root.name)}__{digest}"


def _detect_base_branch(root: Path, explicit: str | None) -> str:
    if explicit:
        return explicit

    origin_head = _optional_git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if origin_head and "/" in origin_head:
        return origin_head.split("/", 1)[1]

    for candidate in ("main", "develop"):
        if _branch_exists(root, candidate):
            return candidate

    raise RepositoryError(
        "基準ブランチを判定できません。--base-branchで明示してください。"
        "自動fetchは行いません。"
    )


def _branch_exists(root: Path, branch: str) -> bool:
    completed = _run_git(root, "rev-parse", "--verify", "--quiet", branch)
    return completed.returncode == 0


def _current_branch(root: Path) -> str:
    branch = _optional_git(root, "branch", "--show-current")
    if branch:
        return branch
    return "(detached HEAD)"


def _git(cwd: Path, *args: str) -> str:
    completed = _run_git(cwd, *args)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RepositoryError(f"Gitリポジトリとして検証できません: {message}")
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _optional_git(cwd: Path, *args: str) -> str | None:
    completed = _run_git(cwd, *args)
    if completed.returncode != 0:
        return None
    value = completed.stdout.decode("utf-8", errors="replace").strip()
    return value or None


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )


def _parse_remote(remote_url: str) -> tuple[str, str, str] | None:
    if remote_url.startswith("git@") and ":" in remote_url:
        host_part, path_part = remote_url[4:].split(":", 1)
        parts = path_part.removesuffix(".git").split("/")
        if len(parts) >= 2:
            return host_part, parts[-2], parts[-1]

    parsed = urlparse(remote_url)
    if parsed.scheme and parsed.netloc:
        parts = parsed.path.strip("/").removesuffix(".git").split("/")
        if len(parts) >= 2:
            return parsed.netloc, parts[-2], parts[-1]

    return None


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return slug or "unknown"
