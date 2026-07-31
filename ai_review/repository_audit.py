from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
import json
import os
import re
import time

from .agents import AGENT_ORDER, AgentResult, Finding, rule_based_decision
from .diff_collector import DEFAULT_EXCLUDES
from .processes import decode_output, run_command
from .repository import RepositoryContext
from .review_engine import CopilotClient, parse_agent_response
from .secrets import combine_secret_scans, scan_payload, SecretScanResult


class AuditError(RuntimeError):
    pass


PROFILE_AGENTS = {
    "quick": {
        "batch": ["correctness", "security"],
        "cross": ["final"],
    },
    "standard": {
        "batch": ["correctness", "security", "testing", "maintainability"],
        "cross": ["requirements", "performance", "operations", "devil_advocate", "final"],
    },
    "deep": {
        "batch": ["requirements", "correctness", "security", "testing", "maintainability", "performance", "operations", "devil_advocate"],
        "cross": ["final"],
    },
}

AUDIT_DEFAULTS = {
    "profile": "standard",
    "max_batches": 30,
    "max_files": 1000,
    "max_total_lines": 100000,
    "max_copilot_calls": 150,
    "max_files_per_batch": 30,
    "max_lines_per_batch": 5000,
    "max_chars_per_batch": 120000,
    "max_file_bytes": 200000,
}

SECRET_FILE_PATTERNS = (
    ".env",
    ".env.",
    ".pem",
    ".key",
    ".p12",
)

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".mp4", ".mov", ".avi",
    ".exe", ".dll", ".bin", ".sqlite", ".db", ".wasm",
}

GENERATED_OR_DATA_EXTENSIONS = {".log", ".coverage", ".mp3", ".wav"}

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".html": "html",
    ".css": "css",
}


@dataclass(frozen=True)
class AuditOptions:
    profile: str = "standard"
    include_untracked: bool = False
    max_batches: int = AUDIT_DEFAULTS["max_batches"]
    max_files: int = AUDIT_DEFAULTS["max_files"]
    max_total_lines: int = AUDIT_DEFAULTS["max_total_lines"]
    max_copilot_calls: int = AUDIT_DEFAULTS["max_copilot_calls"]
    max_files_per_batch: int = AUDIT_DEFAULTS["max_files_per_batch"]
    max_lines_per_batch: int = AUDIT_DEFAULTS["max_lines_per_batch"]
    max_chars_per_batch: int = AUDIT_DEFAULTS["max_chars_per_batch"]
    max_file_bytes: int = AUDIT_DEFAULTS["max_file_bytes"]
    rerun: bool = False
    no_agents: bool = False


@dataclass(frozen=True)
class AuditFile:
    path: str
    absolute_path: Path
    language: str
    lines: int
    chars: int
    tracked: bool
    test_candidate: bool
    related_tests: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CoverageEntry:
    path: str
    status: str
    batch_id: str | None
    executed_agents: list[str]
    reason: str | None


@dataclass(frozen=True)
class AuditBatch:
    batch_id: str
    name: str
    files: list[AuditFile]
    estimated_lines: int
    estimated_chars: int
    agents: list[str]


@dataclass(frozen=True)
class RepositoryAnalysis:
    project_id: str
    repository_path: str
    repository_remote: str | None
    current_branch: str
    head_sha: str
    target_files: list[AuditFile]
    coverage: list[CoverageEntry]
    estimated_total_lines: int
    language_distribution: dict[str, int]
    top_directories: dict[str, int]
    test_file_count: int
    readme_files: list[str]
    document_files: list[str]
    ci_files: list[str]
    expected_batches: int
    expected_copilot_calls: int


@dataclass(frozen=True)
class AuditResult:
    run_id: str
    profile: str
    batches: list[AuditBatch]
    coverage: list[CoverageEntry]
    batch_results: dict[str, list[AgentResult]]
    cross_results: list[AgentResult]
    final_decision: str
    copilot_call_count: int
    max_active_calls: int
    elapsed_seconds: float
    blocked: bool
    failed_batches: list[str]
    blocked_batches: list[str]
    unreviewed_files: list[str]


def validate_profile(profile: str) -> None:
    if profile not in PROFILE_AGENTS:
        raise AuditError(f"不正なprofileです: {profile}")


def analyze_repository(repository: RepositoryContext, options: AuditOptions) -> tuple[RepositoryAnalysis, list[AuditBatch]]:
    validate_profile(options.profile)
    raw_paths = _git_paths(repository.root, include_untracked=False)
    if options.include_untracked:
        raw_paths.extend(_git_paths(repository.root, include_untracked=True))

    files: list[AuditFile] = []
    coverage: list[CoverageEntry] = []
    for raw_path in sorted(dict.fromkeys(raw_paths), key=_path_sort_key):
        entry = _inspect_file(repository.root, raw_path, tracked=raw_path in raw_paths, options=options)
        if isinstance(entry, CoverageEntry):
            coverage.append(entry)
        else:
            files.append(entry)

    _attach_related_tests(files)
    estimated_total_lines = sum(item.lines for item in files)
    batches = split_batches(files, options)
    expected_copilot_calls = sum(len(batch.agents) for batch in batches) + len(PROFILE_AGENTS[options.profile]["cross"])
    if len(files) > options.max_files:
        raise AuditError(f"対象ファイル数が上限を超えています: {len(files)} > {options.max_files}")
    if estimated_total_lines > options.max_total_lines:
        raise AuditError(f"推定総行数が上限を超えています: {estimated_total_lines} > {options.max_total_lines}")
    if len(batches) > options.max_batches:
        raise AuditError(f"バッチ数が上限を超えています: {len(batches)} > {options.max_batches}")
    if expected_copilot_calls > options.max_copilot_calls:
        raise AuditError(f"想定Copilot呼び出し数が上限を超えています: {expected_copilot_calls} > {options.max_copilot_calls}")

    assigned = {file.path: batch.batch_id for batch in batches for file in batch.files}
    for item in files:
        coverage.append(CoverageEntry(item.path, "unreviewed", assigned.get(item.path), [], None))

    analysis = RepositoryAnalysis(
        project_id=repository.project_id,
        repository_path=str(repository.root),
        repository_remote=repository.remote_url,
        current_branch=repository.current_branch,
        head_sha=repository.head_sha,
        target_files=files,
        coverage=coverage,
        estimated_total_lines=estimated_total_lines,
        language_distribution=_count_by(files, lambda item: item.language),
        top_directories=_count_by(files, lambda item: item.path.split("/", 1)[0] if "/" in item.path else "."),
        test_file_count=sum(1 for item in files if item.test_candidate),
        readme_files=[item.path for item in files if PurePosixPath(item.path).name.lower().startswith("readme")],
        document_files=[item.path for item in files if item.path.startswith("docs/") or item.language == "markdown"],
        ci_files=[item.path for item in files if item.path.startswith(".github/workflows/")],
        expected_batches=len(batches),
        expected_copilot_calls=expected_copilot_calls,
    )
    return analysis, batches


def split_batches(files: list[AuditFile], options: AuditOptions) -> list[AuditBatch]:
    grouped: dict[tuple[str, str], list[AuditFile]] = {}
    for file in files:
        top = file.path.split("/", 1)[0] if "/" in file.path else "."
        grouped.setdefault((top, file.language), []).append(file)

    batches: list[AuditBatch] = []
    for (top, language), group in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        current: list[AuditFile] = []
        current_lines = 0
        current_chars = 0
        chunks: list[list[AuditFile]] = []
        for file in sorted(group, key=lambda item: item.path):
            if file.lines > options.max_lines_per_batch or file.chars > options.max_chars_per_batch:
                if current:
                    chunks.append(current)
                    current = []
                    current_lines = 0
                    current_chars = 0
                chunks.extend(_split_large_file(file, options))
                continue
            exceeds = (
                len(current) >= options.max_files_per_batch
                or current_lines + file.lines > options.max_lines_per_batch
                or current_chars + file.chars > options.max_chars_per_batch
            )
            if current and exceeds:
                chunks.append(current)
                current = []
                current_lines = 0
                current_chars = 0
            current.append(file)
            current_lines += file.lines
            current_chars += file.chars
        if current:
            chunks.append(current)
        for index, chunk in enumerate(chunks, start=1):
            suffix = f" {index}/{len(chunks)}" if len(chunks) > 1 else ""
            batch_id = f"batch-{len(batches) + 1:03d}"
            batches.append(
                AuditBatch(
                    batch_id=batch_id,
                    name=f"{top}/{language}{suffix}",
                    files=chunk,
                    estimated_lines=sum(item.lines for item in chunk),
                    estimated_chars=sum(item.chars for item in chunk),
                    agents=list(PROFILE_AGENTS[options.profile]["batch"]),
                )
            )
    return batches


def run_repository_audit(
    repository: RepositoryContext,
    options: AuditOptions,
    *,
    run_id: str,
    client: CopilotClient,
    progress_callback=None,
    cancel_file: Path | None = None,
) -> tuple[RepositoryAnalysis, AuditResult]:
    started = time.monotonic()
    analysis, batches = analyze_repository(repository, options)
    batch_results: dict[str, list[AgentResult]] = {}
    cross_results: list[AgentResult] = []
    coverage = list(analysis.coverage)
    copilot_call_count = 0
    active_calls = 0
    max_active_calls = 0
    failed_batches: list[str] = []
    blocked_batches: list[str] = []

    for batch in batches:
        if scan_payload(_batch_payload(repository, analysis, batch)).blocked:
            blocked_batches.append(batch.batch_id)
            coverage = _mark_coverage(coverage, batch, "blocked", batch.agents, "confirmed_secret")
    if blocked_batches:
        return analysis, _audit_result(
            run_id,
            options,
            batches,
            coverage,
            batch_results,
            cross_results,
            "BLOCKED",
            copilot_call_count,
            max_active_calls,
            started,
            True,
            failed_batches,
            blocked_batches,
        )

    _emit(progress_callback, mode="repository_audit", profile=options.profile, status="running", total_batches=len(batches), pending_batches=[b.batch_id for b in batches])
    for batch_index, batch in enumerate(batches, start=1):
        if _cancelled(cancel_file):
            failed_batches.append(batch.batch_id)
            break
        _emit(
            progress_callback,
            mode="repository_audit",
            profile=options.profile,
            status="running",
            current_batch_id=batch.batch_id,
            current_batch_name=batch.name,
            current_batch_index=batch_index,
            total_batches=len(batches),
            completed_batches=[item.batch_id for item in batches[: batch_index - 1]],
            failed_batches=failed_batches,
            blocked_batches=blocked_batches,
            pending_batches=[item.batch_id for item in batches[batch_index:]],
        )
        payload = _batch_payload(repository, analysis, batch)
        batch_results[batch.batch_id] = []
        if options.no_agents:
            continue
        for agent_index, agent in enumerate(batch.agents, start=1):
            if _cancelled(cancel_file):
                failed_batches.append(batch.batch_id)
                break
            _terminal_progress(batch_index, len(batches), batch, agent_index, len(batch.agents), agent, len(batches[: batch_index - 1]), len(failed_batches), _unreviewed_count(coverage))
            _emit(progress_callback, current_agent=agent, current_agent_index=agent_index, current_batch_total_agents=len(batch.agents), completed_agents=[r.agent for r in batch_results[batch.batch_id]])
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            try:
                raw = client.run_prompt(_prompt(agent, payload, batch_results[batch.batch_id]), timeout_seconds=120)
                batch_results[batch.batch_id].append(parse_agent_response(raw, run_id=run_id, agent=agent))
                copilot_call_count += 1
            except Exception:
                failed_batches.append(batch.batch_id)
                batch_results[batch.batch_id].append(_failed_result(run_id, agent, batch.batch_id))
                break
            finally:
                active_calls -= 1
        if batch.batch_id not in failed_batches and batch.batch_id not in blocked_batches:
            coverage = _mark_coverage(coverage, batch, "reviewed", batch.agents, None)

    if not options.no_agents and not failed_batches and not blocked_batches:
        cross_payload = _cross_payload(repository, analysis, batch_results, coverage)
        for index, agent in enumerate(PROFILE_AGENTS[options.profile]["cross"], start=1):
            if _cancelled(cancel_file):
                failed_batches.append("cross")
                break
            _emit(progress_callback, current_agent=agent, current_agent_index=index, current_batch_total_agents=len(PROFILE_AGENTS[options.profile]["cross"]))
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            try:
                raw = client.run_prompt(_prompt(agent, cross_payload, cross_results), timeout_seconds=120)
                cross_results.append(parse_agent_response(raw, run_id=run_id, agent=agent))
                copilot_call_count += 1
            except Exception:
                failed_batches.append("cross")
                cross_results.append(_failed_result(run_id, agent, "cross"))
                break
            finally:
                active_calls -= 1

    decision = _final_decision(batch_results, cross_results, failed_batches, blocked_batches, coverage)
    return analysis, _audit_result(run_id, options, batches, coverage, batch_results, cross_results, decision, copilot_call_count, max_active_calls, started, False, failed_batches, blocked_batches)


def _git_paths(root: Path, *, include_untracked: bool) -> list[str]:
    args = ["git", "ls-files", "-z"]
    if include_untracked:
        args = ["git", "ls-files", "--others", "--exclude-standard", "-z"]
    completed = run_command(args, cwd=root)
    if completed.returncode != 0:
        raise AuditError(decode_output(completed.stderr).strip())
    return [path for path in decode_output(completed.stdout).split("\0") if path]


def _inspect_file(root: Path, raw_path: str, *, tracked: bool, options: AuditOptions) -> AuditFile | CoverageEntry:
    path = _safe_path(root, raw_path)
    normalized = path.as_posix()
    reason = _exclude_reason(normalized)
    absolute = (root / normalized).resolve()
    if reason:
        return CoverageEntry(normalized, "excluded", None, [], reason)
    if absolute.is_symlink():
        try:
            absolute.resolve(strict=True).relative_to(root.resolve())
        except ValueError:
            return CoverageEntry(normalized, "excluded", None, [], "symlink_outside_repository")
        return CoverageEntry(normalized, "excluded", None, [], "symlink")
    if not absolute.exists() or not absolute.is_file():
        return CoverageEntry(normalized, "skipped", None, [], "not_regular_file")
    size = absolute.stat().st_size
    if size > options.max_file_bytes:
        return CoverageEntry(normalized, "excluded", None, [], "large_file")
    data = absolute.read_bytes()
    if b"\0" in data[:8192]:
        return CoverageEntry(normalized, "excluded", None, [], "binary")
    text = _decode_text(data)
    if text is None:
        return CoverageEntry(normalized, "excluded", None, [], "binary")
    lines = text.splitlines()
    return AuditFile(
        path=normalized,
        absolute_path=absolute,
        language=LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), path.suffix.lower().lstrip(".") or "text"),
        lines=len(lines),
        chars=len(text),
        tracked=tracked,
        test_candidate=_is_test_path(normalized),
    )


def _safe_path(root: Path, raw_path: str) -> PurePosixPath:
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise AuditError(f"リポジトリ外のパスを拒否しました: {raw_path}") from exc
    return PurePosixPath(candidate.relative_to(root.resolve()).as_posix())


def _exclude_reason(path: str) -> str | None:
    lowered = path.lower()
    if any(lowered == item.rstrip("/") or lowered.startswith(item) for item in DEFAULT_EXCLUDES):
        return "default_exclude"
    if "/vendor/" in lowered or lowered.startswith("vendor/"):
        return "vendor"
    if "/coverage/" in lowered or lowered.startswith("coverage/"):
        return "coverage"
    name = PurePosixPath(path).name.lower()
    if name == ".env" or name.startswith(".env.") or any(name.endswith(pattern) for pattern in SECRET_FILE_PATTERNS if pattern.startswith(".")):
        return "secret_file"
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return "binary_extension"
    if suffix in GENERATED_OR_DATA_EXTENSIONS:
        return "generated_or_log"
    return None


def _decode_text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("cp932")
        except UnicodeDecodeError:
            return None


def _attach_related_tests(files: list[AuditFile]) -> None:
    test_paths = [item.path for item in files if item.test_candidate]
    for item in files:
        if item.test_candidate:
            continue
        stem = PurePosixPath(item.path).stem
        related = [path for path in test_paths if stem in PurePosixPath(path).stem or stem in path]
        object.__setattr__(item, "related_tests", related[:5])


def _split_large_file(file: AuditFile, options: AuditOptions) -> list[list[AuditFile]]:
    chunks = max(1, (file.lines + options.max_lines_per_batch - 1) // options.max_lines_per_batch)
    if chunks > 20:
        return []
    return [[file] for _ in range(chunks)]


def _count_by(files: list[AuditFile], key_func) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in files:
        key = key_func(item)
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _is_test_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith("tests/") or "/tests/" in lowered or re.search(r"(^|[_./-])test(s)?[_./-]", lowered) is not None


def _path_sort_key(path: str) -> str:
    return path.replace("\\", "/").casefold()


def _batch_payload(repository: RepositoryContext, analysis: RepositoryAnalysis, batch: AuditBatch) -> dict:
    return {
        "project_id": repository.project_id,
        "mode": "repository_audit",
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "repository_summary": {
            "target_file_count": len(analysis.target_files),
            "estimated_total_lines": analysis.estimated_total_lines,
            "language_distribution": analysis.language_distribution,
        },
        "files": [
            {
                "path": item.path,
                "language": item.language,
                "lines": item.lines,
                "related_tests": item.related_tests,
                "content": _read_file(item),
            }
            for item in batch.files
        ],
    }


def _cross_payload(repository: RepositoryContext, analysis: RepositoryAnalysis, batch_results: dict[str, list[AgentResult]], coverage: list[CoverageEntry]) -> dict:
    return {
        "project_id": repository.project_id,
        "mode": "repository_audit_cross",
        "summary": {
            "target_file_count": len(analysis.target_files),
            "estimated_total_lines": analysis.estimated_total_lines,
            "language_distribution": analysis.language_distribution,
            "top_directories": analysis.top_directories,
            "test_file_count": analysis.test_file_count,
            "expected_batches": analysis.expected_batches,
        },
        "coverage": [asdict(item) for item in coverage],
        "batch_results": {key: [asdict(item) for item in value] for key, value in batch_results.items()},
    }


def _prompt(agent: str, payload: dict, previous: list[AgentResult]) -> str:
    return f"Repository audit agent: {agent}\nReturn JSON only.\nPAYLOAD_JSON\n" + json.dumps(payload | {"previous_results": [asdict(item) for item in previous[-2:]]}, ensure_ascii=False)


def _read_file(file: AuditFile) -> str:
    return _decode_text(file.absolute_path.read_bytes()) or ""


def _mark_coverage(coverage: list[CoverageEntry], batch: AuditBatch, status: str, agents: list[str], reason: str | None) -> list[CoverageEntry]:
    paths = {item.path for item in batch.files}
    return [
        CoverageEntry(item.path, status, batch.batch_id, agents, reason) if item.path in paths else item
        for item in coverage
    ]


def _final_decision(batch_results: dict[str, list[AgentResult]], cross_results: list[AgentResult], failed_batches: list[str], blocked_batches: list[str], coverage: list[CoverageEntry]) -> str:
    if blocked_batches:
        return "BLOCKED"
    if failed_batches or any(item.status in {"unreviewed", "failed", "skipped"} for item in coverage):
        return "INCONCLUSIVE"
    all_results = [result for results in batch_results.values() for result in results] + cross_results
    return rule_based_decision(all_results)


def _audit_result(run_id: str, options: AuditOptions, batches: list[AuditBatch], coverage: list[CoverageEntry], batch_results: dict[str, list[AgentResult]], cross_results: list[AgentResult], decision: str, calls: int, max_active: int, started: float, blocked: bool, failed_batches: list[str], blocked_batches: list[str]) -> AuditResult:
    return AuditResult(
        run_id=run_id,
        profile=options.profile,
        batches=batches,
        coverage=coverage,
        batch_results=batch_results,
        cross_results=cross_results,
        final_decision=decision,
        copilot_call_count=calls,
        max_active_calls=max_active,
        elapsed_seconds=time.monotonic() - started,
        blocked=blocked,
        failed_batches=failed_batches,
        blocked_batches=blocked_batches,
        unreviewed_files=[item.path for item in coverage if item.status in {"unreviewed", "skipped", "failed"}],
    )


def _failed_result(run_id: str, agent: str, batch_id: str) -> AgentResult:
    return AgentResult(run_id, agent, "github-copilot-cli", "0.1.0", "INCONCLUSIVE", [Finding("Info", f"{batch_id} failed")], f"{batch_id} failed", "failed")


def _terminal_progress(batch_index: int, total_batches: int, batch: AuditBatch, agent_index: int, total_agents: int, agent: str, completed_batches: int, failed_batches: int, unreviewed: int) -> None:
    print(f"[ai-review] バッチ [{batch_index}/{total_batches}] {batch.name}")
    print(f"[ai-review] エージェント [{agent_index}/{total_agents}] {agent}")
    print(f"[ai-review] 完了バッチ: {completed_batches}")
    print(f"[ai-review] 失敗バッチ: {failed_batches}")
    print(f"[ai-review] 未確認ファイル: {unreviewed}")


def _emit(callback, **payload) -> None:
    if callback:
        callback(payload)


def _cancelled(cancel_file: Path | None) -> bool:
    return bool(cancel_file and cancel_file.exists())


def _unreviewed_count(coverage: list[CoverageEntry]) -> int:
    return sum(1 for item in coverage if item.status == "unreviewed")
