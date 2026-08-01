from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
import json
import os
import re
import time
from typing import Any

from .agents import AgentResult, Finding, rule_based_decision
from .diff_collector import DEFAULT_EXCLUDES
from .processes import decode_output, run_command
from .repository import RepositoryContext
from .review_engine import CopilotClient, parse_agent_response
from .secrets import scan_payload


class AuditError(RuntimeError):
    pass


PROFILE_AGENTS = {
    "quick": {"batch": ["correctness", "security"], "cross": ["final"]},
    "standard": {
        "batch": ["correctness", "security", "testing", "maintainability"],
        "cross": ["requirements", "performance", "operations", "devil_advocate", "final"],
    },
    # deep runs the eight specialist agents per batch; final runs once in the cross-repository phase.
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

SECRET_FILE_PATTERNS = (".env", ".env.", ".pem", ".key", ".p12")
SECRET_SAMPLE_HINTS = ("fixture", "fixtures", "test", "tests", "sample", "example", "docs", "documentation")
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
SHORT_STEMS = {"api", "db", "io", "app", "cli", "ui", "id", "os"}
TERMINAL_STATUSES = {"completed", "failed", "blocked", "cancelled", "skipped"}


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
    content: str
    related_tests: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuditFileSegment:
    path: str
    absolute_path: Path
    language: str
    start_line: int
    end_line: int
    content: str
    chars: int
    tracked: bool
    test_candidate: bool
    related_tests: list[str] = field(default_factory=list)

    @property
    def lines(self) -> int:
        if self.end_line == 0:
            return 0
        return self.end_line - self.start_line + 1


@dataclass(frozen=True)
class CoverageSegment:
    batch_id: str | None
    start_line: int
    end_line: int
    status: str
    executed_agents: list[str]
    reason: str | None


@dataclass(frozen=True)
class CoverageEntry:
    path: str
    status: str
    batch_id: str | None
    executed_agents: list[str]
    reason: str | None
    segments: list[CoverageSegment] = field(default_factory=list)
    tracked: bool | None = None
    classification: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class AuditBatch:
    batch_id: str
    name: str
    files: list[AuditFileSegment]
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
class CopilotErrorInfo:
    kind: str
    agent: str
    batch_id: str
    message: str
    retryable: bool
    timestamp: float


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
    cancelled_batches: list[str]
    skipped_batches: list[str]
    completed_batches: list[str]
    unreviewed_files: list[str]
    execution_mode: str
    review_completed: bool
    errors: list[CopilotErrorInfo] = field(default_factory=list)


def validate_profile(profile: str) -> None:
    if profile not in PROFILE_AGENTS:
        raise AuditError(f"不正なprofileです: {profile}")


def analyze_repository(repository: RepositoryContext, options: AuditOptions) -> tuple[RepositoryAnalysis, list[AuditBatch]]:
    validate_profile(options.profile)
    tracked_paths = set(_git_paths(repository.root, include_untracked=False))
    untracked_paths = set(_git_paths(repository.root, include_untracked=True)) if options.include_untracked else set()
    all_paths = tracked_paths | untracked_paths

    files: list[AuditFile] = []
    coverage: list[CoverageEntry] = []
    for raw_path in sorted(all_paths, key=_path_sort_key):
        entry = _inspect_file(repository.root, raw_path, tracked=raw_path in tracked_paths, options=options)
        if isinstance(entry, CoverageEntry):
            coverage.append(entry)
        else:
            files.append(entry)

    _attach_related_tests(files)
    estimated_total_lines = sum(item.lines for item in files)
    if len(files) > options.max_files:
        raise AuditError(f"対象ファイル数が上限を超えています: {len(files)} > {options.max_files}")
    if estimated_total_lines > options.max_total_lines:
        raise AuditError(f"推定総行数が上限を超えています: {estimated_total_lines} > {options.max_total_lines}")

    batches, skipped = split_batches_with_coverage(files, options)
    coverage.extend(skipped)
    expected_copilot_calls = sum(len(batch.agents) for batch in batches) + (0 if options.no_agents else len(PROFILE_AGENTS[options.profile]["cross"]))
    if len(batches) > options.max_batches:
        raise AuditError(f"バッチ数が上限を超えています: {len(batches)} > {options.max_batches}")
    if expected_copilot_calls > options.max_copilot_calls:
        raise AuditError(f"想定Copilot呼び出し数が上限を超えています: {expected_copilot_calls} > {options.max_copilot_calls}")

    coverage.extend(_initial_coverage(files, batches))
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
    return split_batches_with_coverage(files, options)[0]


def split_batches_with_coverage(files: list[AuditFile], options: AuditOptions) -> tuple[list[AuditBatch], list[CoverageEntry]]:
    grouped: dict[tuple[str, str], list[AuditFileSegment]] = {}
    skipped: list[CoverageEntry] = []
    for file in files:
        segments, reason = _split_file_segments(file, options)
        if reason:
            skipped.append(_file_coverage(file, "skipped", reason=reason))
            continue
        for segment in segments:
            top = segment.path.split("/", 1)[0] if "/" in segment.path else "."
            grouped.setdefault((top, segment.language), []).append(segment)

    batches: list[AuditBatch] = []
    for (top, language), group in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        current: list[AuditFileSegment] = []
        current_lines = 0
        current_chars = 0
        chunks: list[list[AuditFileSegment]] = []
        for segment in sorted(group, key=lambda item: (item.path, item.start_line)):
            exceeds = (
                len({item.path for item in current}) >= options.max_files_per_batch
                or current_lines + segment.lines > options.max_lines_per_batch
                or current_chars + segment.chars > options.max_chars_per_batch
            )
            if current and exceeds:
                chunks.append(current)
                current = []
                current_lines = 0
                current_chars = 0
            current.append(segment)
            current_lines += segment.lines
            current_chars += segment.chars
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
    return batches, skipped


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
    completed_batches: list[str] = []
    failed_batches: list[str] = []
    blocked_batches: list[str] = [batch.batch_id for batch in batches if _batch_has_blocked_coverage(coverage, batch)]
    cancelled_batches: list[str] = []
    skipped_batches: list[str] = []
    errors: list[CopilotErrorInfo] = []
    pending_batches = [batch.batch_id for batch in batches]

    if any(item.status == "blocked" for item in coverage):
        return analysis, _audit_result(
            run_id, options, batches, coverage, batch_results, cross_results, "BLOCKED", copilot_call_count,
            max_active_calls, started, True, failed_batches, blocked_batches, cancelled_batches, skipped_batches,
            completed_batches, errors,
        )

    for batch in batches:
        if _batch_has_blocked_coverage(coverage, batch):
            coverage = _mark_coverage(coverage, batch, "blocked", [], "confirmed_secret_file")
            continue
        scan = scan_payload(_batch_payload(repository, analysis, batch))
        if scan.blocked:
            blocked_batches.append(batch.batch_id)
            coverage = _mark_coverage(coverage, batch, "blocked", [], "confirmed_secret")
    blocked_batches = _unique(blocked_batches)
    if blocked_batches:
        return analysis, _audit_result(
            run_id, options, batches, coverage, batch_results, cross_results, "BLOCKED", copilot_call_count,
            max_active_calls, started, True, failed_batches, blocked_batches, cancelled_batches, skipped_batches,
            completed_batches, errors,
        )

    if options.no_agents:
        _emit(progress_callback, **_progress_payload(options, "analysis_only", batches, None, pending_batches, completed_batches, failed_batches, blocked_batches, cancelled_batches, skipped_batches))
        return analysis, _audit_result(
            run_id, options, batches, coverage, batch_results, cross_results, "ANALYSIS_ONLY", 0, 0, started, False,
            failed_batches, blocked_batches, cancelled_batches, skipped_batches, completed_batches, errors,
        )

    _emit(progress_callback, **_progress_payload(options, "running", batches, None, pending_batches, completed_batches, failed_batches, blocked_batches, cancelled_batches, skipped_batches))
    for batch_index, batch in enumerate(batches, start=1):
        if _cancelled(cancel_file):
            cancelled_batches.append(batch.batch_id)
            coverage = _mark_coverage(coverage, batch, "cancelled", [], "cancelled")
            skipped_batches.extend(item.batch_id for item in batches[batch_index:] if item.batch_id not in skipped_batches)
            break
        pending_batches = [item.batch_id for item in batches[batch_index - 1 :] if item.batch_id not in completed_batches + failed_batches + blocked_batches + cancelled_batches]
        _emit(progress_callback, **_progress_payload(options, "running", batches, batch, pending_batches, completed_batches, failed_batches, blocked_batches, cancelled_batches, skipped_batches, batch_index=batch_index))
        batch_results[batch.batch_id] = []
        batch_failed = False
        batch_cancelled = False
        for agent_index, agent in enumerate(batch.agents, start=1):
            if _cancelled(cancel_file):
                cancelled_batches.append(batch.batch_id)
                coverage = _mark_coverage(coverage, batch, "cancelled", [r.agent for r in batch_results[batch.batch_id]], "cancelled")
                batch_cancelled = True
                break
            _terminal_progress(batch_index, len(batches), batch, agent_index, len(batch.agents), agent, len(completed_batches), len(failed_batches), _unreviewed_count(coverage))
            _emit(
                progress_callback,
                **_progress_payload(
                    options, "running", batches, batch, pending_batches, completed_batches, failed_batches,
                    blocked_batches, cancelled_batches, skipped_batches, batch_index=batch_index, agent=agent,
                    agent_index=agent_index, completed_agents=[r.agent for r in batch_results[batch.batch_id]],
                ),
            )
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            try:
                raw = client.run_prompt(_prompt(agent, _batch_payload(repository, analysis, batch), batch_results[batch.batch_id]), timeout_seconds=120)
                batch_results[batch.batch_id].append(parse_agent_response(raw, run_id=run_id, agent=agent))
                copilot_call_count += 1
            except Exception as exc:  # noqa: BLE001 - stored as sanitized audit error.
                info = _classify_copilot_exception(exc, agent=agent, batch_id=batch.batch_id)
                errors.append(info)
                failed_batches.append(batch.batch_id)
                batch_results[batch.batch_id].append(_failed_result(run_id, agent, batch.batch_id, info))
                coverage = _mark_coverage(coverage, batch, "failed", [r.agent for r in batch_results[batch.batch_id]], info.kind)
                batch_failed = True
                break
            finally:
                active_calls -= 1
        if batch_cancelled or batch_failed:
            break
        if batch.batch_id not in completed_batches:
            completed_batches.append(batch.batch_id)
            coverage = _mark_coverage(coverage, batch, "reviewed", batch.agents, None)

    if cancelled_batches:
        started_batch_ids = set(completed_batches + failed_batches + blocked_batches + cancelled_batches)
        for batch in batches:
            if batch.batch_id not in started_batch_ids:
                skipped_batches.append(batch.batch_id)
                coverage = _mark_coverage(coverage, batch, "skipped", [], "cancelled_before_start")

    if not failed_batches and not blocked_batches and not cancelled_batches:
        cross_payload = _cross_payload(repository, analysis, batch_results, coverage)
        for index, agent in enumerate(PROFILE_AGENTS[options.profile]["cross"], start=1):
            if _cancelled(cancel_file):
                cancelled_batches.append("cross")
                break
            _emit(progress_callback, current_agent=agent, current_agent_index=index, current_batch_total_agents=len(PROFILE_AGENTS[options.profile]["cross"]))
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            try:
                raw = client.run_prompt(_prompt(agent, cross_payload, cross_results), timeout_seconds=120)
                cross_results.append(parse_agent_response(raw, run_id=run_id, agent=agent))
                copilot_call_count += 1
            except Exception as exc:  # noqa: BLE001
                info = _classify_copilot_exception(exc, agent=agent, batch_id="cross")
                errors.append(info)
                failed_batches.append("cross")
                cross_results.append(_failed_result(run_id, agent, "cross", info))
                break
            finally:
                active_calls -= 1

    decision = _final_decision(batch_results, cross_results, failed_batches, blocked_batches, cancelled_batches, coverage)
    status = "cancelled" if cancelled_batches else "failed" if failed_batches else "blocked" if blocked_batches else "completed"
    _emit(progress_callback, **_progress_payload(options, status, batches, None, [], completed_batches, failed_batches, blocked_batches, cancelled_batches, skipped_batches))
    return analysis, _audit_result(
        run_id, options, batches, coverage, batch_results, cross_results, decision, copilot_call_count, max_active_calls,
        started, bool(blocked_batches), failed_batches, blocked_batches, cancelled_batches, skipped_batches,
        completed_batches, errors,
    )


def _git_paths(root: Path, *, include_untracked: bool) -> list[str]:
    args = ["git", "ls-files", "-z"]
    if include_untracked:
        args = ["git", "ls-files", "--others", "--exclude-standard", "-z"]
    completed = run_command(args, cwd=root)
    if completed.returncode != 0:
        raise AuditError(decode_output(completed.stderr).strip())
    return [path for path in decode_output(completed.stdout).split("\0") if path]


def _inspect_file(root: Path, raw_path: str, *, tracked: bool, options: AuditOptions) -> AuditFile | CoverageEntry:
    normalized = _normalize_raw_path(root, raw_path)
    reason = _exclude_reason(normalized)
    unresolved = root / normalized
    if unresolved.is_symlink():
        try:
            resolved = unresolved.resolve(strict=True)
            resolved.relative_to(root.resolve())
        except (OSError, RuntimeError):
            return CoverageEntry(normalized, "excluded", None, [], "broken_or_loop_symlink", tracked=tracked)
        except ValueError:
            return CoverageEntry(normalized, "excluded", None, [], "symlink_outside_repository", tracked=tracked)
        return CoverageEntry(normalized, "excluded", None, [], "symlink", tracked=tracked)
    if reason:
        status = "blocked" if reason == "confirmed_secret_file" else "excluded"
        return CoverageEntry(normalized, status, None, [], reason, tracked=tracked, classification="confirmed" if status == "blocked" else None, category="secret_file" if status == "blocked" else None)
    try:
        absolute = unresolved.resolve(strict=True)
        absolute.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise AuditError(f"リポジトリ外または不正なパスを拒否しました: {raw_path}") from exc
    if not absolute.is_file():
        return CoverageEntry(normalized, "skipped", None, [], "not_regular_file", tracked=tracked)
    size = absolute.stat().st_size
    if size > options.max_file_bytes:
        return CoverageEntry(normalized, "excluded", None, [], "large_file", tracked=tracked)
    data = absolute.read_bytes()
    if b"\0" in data[:8192]:
        return CoverageEntry(normalized, "excluded", None, [], "binary", tracked=tracked)
    text = _decode_text(data)
    if text is None:
        return CoverageEntry(normalized, "excluded", None, [], "binary", tracked=tracked)
    return AuditFile(
        path=normalized,
        absolute_path=absolute,
        language=LANGUAGE_BY_SUFFIX.get(PurePosixPath(normalized).suffix.lower(), PurePosixPath(normalized).suffix.lower().lstrip(".") or "text"),
        lines=_line_count(text),
        chars=len(text),
        tracked=tracked,
        test_candidate=_is_test_path(normalized),
        content=text,
    )


def _normalize_raw_path(root: Path, raw_path: str) -> str:
    raw = raw_path.replace("\\", "/")
    if raw.startswith("/") or "\0" in raw:
        raise AuditError(f"不正なパスを拒否しました: {raw_path}")
    candidate = PurePosixPath(raw)
    if any(part == ".." for part in candidate.parts):
        raise AuditError(f"パストラバーサルを拒否しました: {raw_path}")
    unresolved = root / candidate
    try:
        unresolved.parent.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise AuditError(f"リポジトリ外のパスを拒否しました: {raw_path}") from exc
    return candidate.as_posix()


def _exclude_reason(path: str) -> str | None:
    lowered = path.lower()
    if any(lowered == item.rstrip("/") or lowered.startswith(item) for item in DEFAULT_EXCLUDES):
        return "default_exclude"
    if "/vendor/" in lowered or lowered.startswith("vendor/"):
        return "vendor"
    if "/coverage/" in lowered or lowered.startswith("coverage/"):
        return "coverage"
    name = PurePosixPath(path).name.lower()
    if name == ".env" or name.startswith(".env.") or name.endswith((".key", ".pem", ".p12")):
        if any(hint in lowered for hint in SECRET_SAMPLE_HINTS):
            return "secret_sample_excluded"
        return "confirmed_secret_file"
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


def _line_count(text: str) -> int:
    if text == "":
        return 0
    return len(text.splitlines())


def _attach_related_tests(files: list[AuditFile]) -> None:
    tests = [item for item in files if item.test_candidate]
    for item in files:
        if item.test_candidate:
            continue
        stem = PurePosixPath(item.path).stem
        related: list[str] = []
        exact_names = {f"test_{stem}.py", f"{stem}_test.py"}
        related.extend(test.path for test in tests if PurePosixPath(test.path).name in exact_names)
        if len(stem) > 2:
            related.extend(test.path for test in tests if PurePosixPath(test.path).stem == stem and test.path not in related)
        item_dir = PurePosixPath(item.path).parent
        related.extend(test.path for test in tests if _same_package(item_dir, PurePosixPath(test.path).parent) and test.path not in related)
        if stem not in SHORT_STEMS and len(stem) > 3:
            related.extend(test.path for test in tests if stem in PurePosixPath(test.path).stem and test.path not in related)
        object.__setattr__(item, "related_tests", related[:5])


def _same_package(left: PurePosixPath, right: PurePosixPath) -> bool:
    left_parts = left.parts
    right_parts = right.parts
    return bool(left_parts and right_parts and left_parts[0] == right_parts[0])


def _split_file_segments(file: AuditFile, options: AuditOptions) -> tuple[list[AuditFileSegment], str | None]:
    if file.content == "":
        return [_segment(file, 0, 0, "")], None
    lines = file.content.splitlines(keepends=True)
    segments: list[AuditFileSegment] = []
    start = 0
    while start < len(lines):
        chars = 0
        end = start
        while end < len(lines) and end - start < options.max_lines_per_batch and chars + len(lines[end]) <= options.max_chars_per_batch:
            chars += len(lines[end])
            end += 1
        if end == start:
            return [], "single_line_exceeds_char_limit"
        content = "".join(lines[start:end])
        segments.append(_segment(file, start + 1, end, content))
        start = end
    return segments, None


def _segment(file: AuditFile, start_line: int, end_line: int, content: str) -> AuditFileSegment:
    return AuditFileSegment(
        path=file.path,
        absolute_path=file.absolute_path,
        language=file.language,
        start_line=start_line,
        end_line=end_line,
        content=content,
        chars=len(content),
        tracked=file.tracked,
        test_candidate=file.test_candidate,
        related_tests=file.related_tests,
    )


def _initial_coverage(files: list[AuditFile], batches: list[AuditBatch]) -> list[CoverageEntry]:
    by_path: dict[str, list[CoverageSegment]] = {file.path: [] for file in files}
    for batch in batches:
        for segment in batch.files:
            by_path.setdefault(segment.path, []).append(CoverageSegment(batch.batch_id, segment.start_line, segment.end_line, "unreviewed", [], None))
    file_by_path = {file.path: file for file in files}
    return [
        CoverageEntry(path, _aggregate_status(segments), segments[0].batch_id if len(segments) == 1 else None, [], None, segments, tracked=file_by_path[path].tracked)
        for path, segments in sorted(by_path.items())
        if segments
    ]


def _file_coverage(file: AuditFile, status: str, *, reason: str) -> CoverageEntry:
    segment = CoverageSegment(None, 1 if file.lines else 0, file.lines, status, [], reason)
    return CoverageEntry(file.path, status, None, [], reason, [segment], tracked=file.tracked)


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
                "start_line": item.start_line,
                "end_line": item.end_line,
                "language": item.language,
                "tracked": item.tracked,
                "related_tests": item.related_tests,
                "content": item.content,
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


def _mark_coverage(coverage: list[CoverageEntry], batch: AuditBatch, status: str, agents: list[str], reason: str | None) -> list[CoverageEntry]:
    ranges = {(item.path, item.start_line, item.end_line) for item in batch.files}
    result: list[CoverageEntry] = []
    for entry in coverage:
        updated_segments = [
            CoverageSegment(segment.batch_id, segment.start_line, segment.end_line, status, agents, reason)
            if (entry.path, segment.start_line, segment.end_line) in ranges
            else segment
            for segment in entry.segments
        ]
        if updated_segments == entry.segments:
            result.append(entry)
            continue
        result.append(
            CoverageEntry(
                entry.path,
                _aggregate_status(updated_segments),
                _single_batch(updated_segments),
                _aggregate_agents(updated_segments),
                _aggregate_reason(updated_segments),
                updated_segments,
                tracked=entry.tracked,
                classification=entry.classification,
                category=entry.category,
            )
        )
    return result


def _aggregate_status(segments: list[CoverageSegment]) -> str:
    statuses = [segment.status for segment in segments]
    for status in ("blocked", "failed", "cancelled", "unreviewed", "skipped"):
        if status in statuses:
            return status
    if statuses and all(status == "excluded" for status in statuses):
        return "excluded"
    if statuses and all(status == "reviewed" for status in statuses):
        return "reviewed"
    return statuses[0] if statuses else "unreviewed"


def _single_batch(segments: list[CoverageSegment]) -> str | None:
    batch_ids = {segment.batch_id for segment in segments if segment.batch_id}
    return next(iter(batch_ids)) if len(batch_ids) == 1 else None


def _aggregate_agents(segments: list[CoverageSegment]) -> list[str]:
    agents: list[str] = []
    for segment in segments:
        for agent in segment.executed_agents:
            if agent not in agents:
                agents.append(agent)
    return agents


def _aggregate_reason(segments: list[CoverageSegment]) -> str | None:
    for status in ("blocked", "failed", "cancelled", "unreviewed", "skipped"):
        for segment in segments:
            if segment.status == status and segment.reason:
                return segment.reason
    return None


def _final_decision(batch_results: dict[str, list[AgentResult]], cross_results: list[AgentResult], failed_batches: list[str], blocked_batches: list[str], cancelled_batches: list[str], coverage: list[CoverageEntry]) -> str:
    if blocked_batches or any(item.status == "blocked" for item in coverage):
        return "BLOCKED"
    if cancelled_batches or failed_batches or any(item.status in {"unreviewed", "failed", "skipped", "cancelled"} for item in coverage):
        return "INCONCLUSIVE"
    all_results = [result for results in batch_results.values() for result in results] + cross_results
    return rule_based_decision(all_results)


def _audit_result(
    run_id: str,
    options: AuditOptions,
    batches: list[AuditBatch],
    coverage: list[CoverageEntry],
    batch_results: dict[str, list[AgentResult]],
    cross_results: list[AgentResult],
    decision: str,
    calls: int,
    max_active: int,
    started: float,
    blocked: bool,
    failed_batches: list[str],
    blocked_batches: list[str],
    cancelled_batches: list[str],
    skipped_batches: list[str],
    completed_batches: list[str],
    errors: list[CopilotErrorInfo],
) -> AuditResult:
    execution_mode = "analysis_only" if options.no_agents else "review"
    review_completed = execution_mode == "review" and not (failed_batches or blocked_batches or cancelled_batches) and not any(item.status in {"unreviewed", "skipped", "failed", "blocked", "cancelled"} for item in coverage)
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
        failed_batches=_unique(failed_batches),
        blocked_batches=_unique(blocked_batches),
        cancelled_batches=_unique(cancelled_batches),
        skipped_batches=_unique(skipped_batches),
        completed_batches=_unique(completed_batches),
        unreviewed_files=[item.path for item in coverage if item.status in {"unreviewed", "skipped", "failed", "cancelled"}],
        execution_mode=execution_mode,
        review_completed=review_completed,
        errors=errors,
    )


def _failed_result(run_id: str, agent: str, batch_id: str, error: CopilotErrorInfo) -> AgentResult:
    return AgentResult(run_id, agent, "github-copilot-cli", "0.1.0", "INCONCLUSIVE", [Finding("Info", f"{batch_id} {error.kind}")], f"{batch_id} {error.kind}", "failed")


def _classify_copilot_exception(exc: Exception, *, agent: str, batch_id: str) -> CopilotErrorInfo:
    text = str(exc).lower()
    name = type(exc).__name__.lower()
    if "cancel" in text or "cancel" in name:
        kind, retryable = "cancelled", False
    elif "auth" in text or "permission" in text:
        kind, retryable = "authentication", False
    elif "rate" in text or "limit" in text:
        kind, retryable = "rate_limit", True
    elif "timeout" in text or "timed out" in text:
        kind, retryable = "timeout", True
    elif "schema" in text or "json" in text or "validation" in text:
        kind, retryable = "schema_validation", True
    elif "start" in text or "not found" in text or "winerror 2" in text:
        kind, retryable = "process_start", False
    elif "network" in text or "connection" in text or "dns" in text:
        kind, retryable = "network", True
    else:
        kind, retryable = "unexpected", False
    return CopilotErrorInfo(kind, agent, batch_id, _safe_error_message(exc), retryable, time.time())


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:200] or type(exc).__name__


def _terminal_progress(batch_index: int, total_batches: int, batch: AuditBatch, agent_index: int, total_agents: int, agent: str, completed_batches: int, failed_batches: int, unreviewed: int) -> None:
    print(f"[ai-review] バッチ [{batch_index}/{total_batches}] {batch.name}")
    print(f"[ai-review] エージェント [{agent_index}/{total_agents}] {agent}")
    print(f"[ai-review] 完了バッチ: {completed_batches}")
    print(f"[ai-review] 失敗バッチ: {failed_batches}")
    print(f"[ai-review] 未確認ファイル: {unreviewed}")


def _progress_payload(
    options: AuditOptions,
    status: str,
    batches: list[AuditBatch],
    batch: AuditBatch | None,
    pending_batches: list[str],
    completed_batches: list[str],
    failed_batches: list[str],
    blocked_batches: list[str],
    cancelled_batches: list[str],
    skipped_batches: list[str],
    *,
    batch_index: int | None = None,
    agent: str | None = None,
    agent_index: int | None = None,
    completed_agents: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "mode": "repository_audit",
        "profile": options.profile,
        "status": status,
        "current_batch_id": batch.batch_id if batch else None,
        "current_batch_name": batch.name if batch else None,
        "current_batch_index": batch_index,
        "total_batches": len(batches),
        "current_agent": agent,
        "current_agent_index": agent_index,
        "current_batch_total_agents": len(batch.agents) if batch else None,
        "completed_batches": _unique(completed_batches),
        "failed_batches": _unique(failed_batches),
        "blocked_batches": _unique(blocked_batches),
        "cancelled_batches": _unique(cancelled_batches),
        "skipped_batches": _unique(skipped_batches),
        "pending_batches": _unique(pending_batches),
        "completed_agents": completed_agents or [],
    }


def _emit(callback, **payload) -> None:
    if callback:
        callback(payload)


def _cancelled(cancel_file: Path | None) -> bool:
    return bool(cancel_file and cancel_file.exists())


def _unreviewed_count(coverage: list[CoverageEntry]) -> int:
    return sum(1 for item in coverage if item.status == "unreviewed")


def _batch_has_blocked_coverage(coverage: list[CoverageEntry], batch: AuditBatch) -> bool:
    paths = {segment.path for segment in batch.files}
    return any(item.path in paths and item.status == "blocked" for item in coverage)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
