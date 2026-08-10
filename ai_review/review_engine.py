from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import subprocess
import time
import uuid

from .agents import AGENT_ORDER, AgentResult, Finding, rule_based_decision, stricter_decision
from .copilot import classify_copilot_error, resolve_copilot_command
from .diff_collector import DiffSummary
from .evaluation import (
    DEFAULT_MAX_PARALLEL_REVIEWERS,
    ExecutionStrategy,
    validate_execution_strategy,
    validate_max_parallel_reviewers,
)
from .processes import decode_output
from .quality import QualityCheckResult
from .repository import RepositoryContext
from .secrets import scan_diff_for_secrets


SPECIALIST_AGENTS = [agent for agent in AGENT_ORDER if agent != "final"]
VALID_EXECUTION_MODES = {"subagent", "legacy"}
VALID_REVIEWER_STATES = {
    "pending",
    "running",
    "delegated",
    "completed",
    "failed",
    "missing",
    "skipped",
    "not_run",
    "blocked",
    "inconclusive",
    "cancelled",
}
INCOMPLETE_REVIEWER_STATES = VALID_REVIEWER_STATES - {"completed"}


class ReviewEngineError(RuntimeError):
    pass


class AgentSchemaError(ReviewEngineError):
    pass


class AgentRunIdMismatchError(ReviewEngineError):
    pass


class AgentCancelledError(ReviewEngineError):
    pass


@dataclass(frozen=True)
class EngineRequest:
    repository: RepositoryContext
    diff: DiffSummary
    quality_checks: list[QualityCheckResult]
    target: str
    run_id: str
    agent: str | None = None
    timeout_seconds: int = 120
    cancel_file: Path | None = None
    execution_mode: str = "subagent"
    orchestration_strategy: ExecutionStrategy = "native"
    max_parallel_reviewers: int = DEFAULT_MAX_PARALLEL_REVIEWERS


@dataclass(frozen=True)
class EngineResult:
    run_id: str
    provider: str
    agent_states: dict[str, str]
    agent_results: list[AgentResult]
    final_decision: str
    max_concurrent_copilot_processes: int
    execution_mode: str = "subagent"
    execution_strategy: str = "native"
    requested_execution_strategy: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    agent_durations_ms: dict[str, int] | None = None
    orchestrator_duration_ms: int | None = None
    final_reviewer_duration_ms: int | None = None


class CopilotClient:
    provider = "github-copilot-cli"

    def run_prompt(self, prompt: str, *, timeout_seconds: int) -> str:
        command = resolve_copilot_command([])
        completed = subprocess.run(
            command.command,
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_seconds,
            shell=False,
        )
        if completed.returncode != 0:
            stderr = decode_output(completed.stderr)
            category = classify_copilot_error(stderr)
            if category == "rate_limit":
                raise ReviewEngineError("Copilot CLI rate limit")
            if category == "auth":
                raise ReviewEngineError("Copilot CLI authentication failed")
            raise ReviewEngineError(f"Copilot CLI failed: {category}")
        return decode_output(completed.stdout)


def run_review_engine(request: EngineRequest, client: CopilotClient | None = None) -> EngineResult:
    """Run the standard Python Review Controller path.

    Python prepares safe review context, invokes the Copilot Review Orchestrator
    once, validates the untrusted Final Reviewer result, reconciles it with
    deterministic rules, and returns data ready for persistence.
    """

    started_at = _now_iso()
    started_monotonic = time.monotonic()
    if request.execution_mode == "legacy":
        return run_legacy_review_engine(request, client, started_at=started_at, started_monotonic=started_monotonic)
    if request.execution_mode not in VALID_EXECUTION_MODES:
        raise ReviewEngineError(f"unknown execution mode: {request.execution_mode}")
    if request.agent and request.agent not in AGENT_ORDER:
        raise ReviewEngineError(f"unknown agent: {request.agent}")

    client = client or CopilotClient()
    blocked = _preflight_blocked_result(request, provider=client.provider, execution_mode="subagent")
    if blocked:
        return _with_timing(
            blocked,
            started_at=started_at,
            started_monotonic=started_monotonic,
            agent_durations_ms={},
            orchestrator_duration_ms=0,
            final_reviewer_duration_ms=None,
            execution_strategy="native",
            requested_execution_strategy="native",
        )
    if _is_cancelled(request.cancel_file):
        raise AgentCancelledError(f"review was cancelled before Copilot invocation: {request.run_id}")

    states = {agent: "delegated" for agent in SPECIALIST_AGENTS}
    states["final"] = "pending"
    results: list[AgentResult] = []
    failed = False
    max_concurrent = 0
    orchestrator_started = time.monotonic()

    try:
        states["final"] = "running"
        max_concurrent = 1
        raw = client.run_prompt(_build_orchestrator_prompt(request), timeout_seconds=request.timeout_seconds)
        final_result = parse_subagent_final_response(raw, run_id=request.run_id)
        results.append(final_result)
        states["final"] = final_result.status
        states.update(_normalize_reviewer_states(final_result.reviewer_states))
        failed = final_result.status in {"failed", "blocked", "inconclusive"} or _has_incomplete_reviewer_state(states)
    except subprocess.TimeoutExpired:
        states["final"] = "failed"
        failed = True
        results.append(_failed_result(request.run_id, "final", "timeout"))
    except (AgentSchemaError, AgentRunIdMismatchError, ReviewEngineError):
        states["final"] = "failed"
        failed = True
        results.append(_failed_result(request.run_id, "final", "failed"))

    return _reconcile_result(
        request,
        provider=client.provider,
        states=states,
        results=results,
        failed=failed,
        max_concurrent=max_concurrent,
        execution_mode="subagent",
        execution_strategy="native",
        requested_execution_strategy="native",
        started_at=started_at,
        started_monotonic=started_monotonic,
        agent_durations_ms={"final": _elapsed_ms(orchestrator_started)},
        orchestrator_duration_ms=_elapsed_ms(orchestrator_started),
        final_reviewer_duration_ms=_elapsed_ms(orchestrator_started),
    )


def run_legacy_review_engine(
    request: EngineRequest,
    client: CopilotClient | None = None,
    *,
    started_at: str | None = None,
    started_monotonic: float | None = None,
) -> EngineResult:
    """Deprecated Python-driven serial reviewer runner.

    This path exists only for migration compatibility. New code must use the
    default `subagent` execution mode, where Python prepares, validates,
    decides, and persists while Copilot Custom Agents review and synthesize.

    Removal condition: delete this path after downstream CLI users no longer
    require Python to invoke the old logical prompts under `agents/*.md`.
    Removed responsibilities will be the 8 specialist reviewer invocations,
    AI specialist judgment generation, Python-side reviewer result handoff, and
    Python-side Final Reviewer invocation.
    """

    client = client or CopilotClient()
    strategy = validate_execution_strategy(request.orchestration_strategy)
    actual_strategy = _actual_legacy_strategy(strategy)
    max_parallel_reviewers = validate_max_parallel_reviewers(request.max_parallel_reviewers)
    started_at = started_at or _now_iso()
    started_monotonic = started_monotonic or time.monotonic()
    blocked = _preflight_blocked_result(request, provider=client.provider, execution_mode="legacy")
    if blocked:
        return _with_timing(
            blocked,
            started_at=started_at,
            started_monotonic=started_monotonic,
            agent_durations_ms={},
            orchestrator_duration_ms=0,
            final_reviewer_duration_ms=None,
            execution_strategy=actual_strategy,
            requested_execution_strategy=strategy,
        )
    selected_agents = _select_agents(request.agent)
    if request.agent and request.agent not in AGENT_ORDER:
        raise ReviewEngineError(f"unknown agent: {request.agent}")

    states = {agent: "pending" for agent in AGENT_ORDER}
    results: list[AgentResult] = []
    durations: dict[str, int] = {}
    specialists = [agent for agent in selected_agents if agent != "final"]
    orchestrator_started = time.monotonic()
    if actual_strategy == "limited_parallel" and len(specialists) > 1:
        max_concurrent = min(max_parallel_reviewers, len(specialists))
        failed = _run_specialists_limited_parallel(
            request,
            client,
            specialists,
            states,
            results,
            durations,
            max_concurrent,
        )
    else:
        max_concurrent = _run_specialists_sequential(
            request,
            client,
            specialists,
            states,
            results,
            durations,
        )
        failed = any(result.status == "failed" for result in results)
    orchestrator_duration_ms = _elapsed_ms(orchestrator_started)

    if "final" in selected_agents:
        _raise_if_cancelled(request, states, "final")
        states["final"] = "running"
        final_started = time.monotonic()
        try:
            prompt = _build_prompt(request, "final", results)
            result = _run_agent_with_retry(client, prompt, request, "final")
            results.append(result)
            states["final"] = "completed"
        except subprocess.TimeoutExpired:
            states["final"] = "failed"
            failed = True
            results.append(_failed_result(request.run_id, "final", "timeout"))
        except ReviewEngineError:
            states["final"] = "failed"
            failed = True
            results.append(_failed_result(request.run_id, "final", "failed"))
        finally:
            durations["final"] = _elapsed_ms(final_started)

    for agent in AGENT_ORDER:
        if agent not in selected_agents and states[agent] == "pending":
            states[agent] = "skipped"

    return _reconcile_result(
        request,
        provider=client.provider,
        states=states,
        results=results,
        failed=failed,
        max_concurrent=max_concurrent,
        execution_mode="legacy",
        execution_strategy=actual_strategy,
        requested_execution_strategy=strategy,
        started_at=started_at,
        started_monotonic=started_monotonic,
        agent_durations_ms=durations,
        orchestrator_duration_ms=orchestrator_duration_ms,
        final_reviewer_duration_ms=durations.get("final"),
    )


def _reconcile_result(
    request: EngineRequest,
    *,
    provider: str,
    states: dict[str, str],
    results: list[AgentResult],
    failed: bool,
    max_concurrent: int,
    execution_mode: str,
    execution_strategy: str,
    requested_execution_strategy: str,
    started_at: str,
    started_monotonic: float,
    agent_durations_ms: dict[str, int],
    orchestrator_duration_ms: int | None,
    final_reviewer_duration_ms: int | None,
) -> EngineResult:
    ai_decision = results[-1].decision if results else "INCONCLUSIVE"
    rules = rule_based_decision(
        results,
        truncated=request.diff.truncated,
        failed=failed or _quality_failed(request.quality_checks),
    )
    return EngineResult(
        run_id=request.run_id,
        provider=provider,
        agent_states=states,
        agent_results=results,
        final_decision=stricter_decision(rules, ai_decision),
        max_concurrent_copilot_processes=max_concurrent,
        execution_mode=execution_mode,
        execution_strategy=execution_strategy,
        requested_execution_strategy=requested_execution_strategy,
        started_at=started_at,
        finished_at=_now_iso(),
        duration_ms=_elapsed_ms(started_monotonic),
        agent_durations_ms=agent_durations_ms,
        orchestrator_duration_ms=orchestrator_duration_ms,
        final_reviewer_duration_ms=final_reviewer_duration_ms,
    )


def _preflight_blocked_result(request: EngineRequest, *, provider: str, execution_mode: str) -> EngineResult | None:
    secret_scan = scan_diff_for_secrets(request.diff)
    if not secret_scan.blocked:
        return None
    return EngineResult(
        run_id=request.run_id,
        provider=provider,
        agent_states={agent: "skipped" for agent in AGENT_ORDER},
        agent_results=[],
        final_decision="BLOCKED",
        max_concurrent_copilot_processes=0,
        execution_mode=execution_mode,
    )


def _run_specialists_sequential(
    request: EngineRequest,
    client: CopilotClient,
    agents: list[str],
    states: dict[str, str],
    results: list[AgentResult],
    durations: dict[str, int],
) -> int:
    max_concurrent = 0
    for agent in agents:
        _raise_if_cancelled(request, states, agent)
        states[agent] = "running"
        max_concurrent = max(max_concurrent, 1)
        started = time.monotonic()
        try:
            prompt = _build_prompt(request, agent, [])
            result = _run_agent_with_retry(client, prompt, request, agent)
            results.append(result)
            states[agent] = "completed"
        except subprocess.TimeoutExpired:
            states[agent] = "failed"
            results.append(_failed_result(request.run_id, agent, "timeout"))
        except ReviewEngineError:
            states[agent] = "failed"
            results.append(_failed_result(request.run_id, agent, "failed"))
        finally:
            durations[agent] = _elapsed_ms(started)
    return max_concurrent


def _run_specialists_limited_parallel(
    request: EngineRequest,
    client: CopilotClient,
    agents: list[str],
    states: dict[str, str],
    results: list[AgentResult],
    durations: dict[str, int],
    max_workers: int,
) -> bool:
    failed = False
    completed: dict[str, AgentResult] = {}
    pending_agents = deque(agents)
    futures: dict[Future[tuple[AgentResult, int]], str] = {}
    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        for _ in range(min(max_workers, len(pending_agents))):
            _submit_next_specialist(request, client, states, pending_agents, futures, executor)
        while futures:
            if _is_cancelled(request.cancel_file):
                _cancel_parallel_work(request, states, pending_agents, futures)
            done, _ = wait(futures, timeout=0.05, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                agent = futures.pop(future)
                if future.cancelled():
                    states[agent] = "cancelled"
                    failed = True
                    continue
                result, duration_ms = future.result()
                durations[agent] = duration_ms
                completed[agent] = result
                states[agent] = "completed" if result.status != "failed" else "failed"
                failed = failed or result.status == "failed"
            while pending_agents and len(futures) < max_workers:
                if _is_cancelled(request.cancel_file):
                    _cancel_parallel_work(request, states, pending_agents, futures)
                _submit_next_specialist(request, client, states, pending_agents, futures, executor)
    except AgentCancelledError:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True, cancel_futures=True)
    for agent in agents:
        if agent in completed:
            results.append(completed[agent])
    return failed


def _submit_next_specialist(
    request: EngineRequest,
    client: CopilotClient,
    states: dict[str, str],
    pending_agents: deque[str],
    futures: dict[Future[tuple[AgentResult, int]], str],
    executor: ThreadPoolExecutor,
) -> None:
    agent = pending_agents.popleft()
    _raise_if_cancelled(request, states, agent)
    states[agent] = "running"
    futures[executor.submit(_run_one_specialist, request, client, agent)] = agent


def _cancel_parallel_work(
    request: EngineRequest,
    states: dict[str, str],
    pending_agents: deque[str],
    futures: dict[Future[tuple[AgentResult, int]], str],
) -> None:
    for agent in pending_agents:
        states[agent] = "cancelled"
    pending_agents.clear()
    for future, agent in list(futures.items()):
        states[agent] = "cancelled"
        future.cancel()
    raise AgentCancelledError(f"review was cancelled: {request.run_id}")


def _run_one_specialist(
    request: EngineRequest,
    client: CopilotClient,
    agent: str,
) -> tuple[AgentResult, int]:
    started = time.monotonic()
    try:
        prompt = _build_prompt(request, agent, [])
        return _run_agent_with_retry(client, prompt, request, agent), _elapsed_ms(started)
    except subprocess.TimeoutExpired:
        return _failed_result(request.run_id, agent, "timeout"), _elapsed_ms(started)
    except ReviewEngineError:
        return _failed_result(request.run_id, agent, "failed"), _elapsed_ms(started)


def _select_agents(requested_agent: str | None) -> list[str]:
    if requested_agent is None:
        return list(AGENT_ORDER)
    if requested_agent == "final":
        return ["final"]
    return [requested_agent, "final"]


def _actual_legacy_strategy(strategy: str) -> str:
    if strategy == "native":
        return "sequential"
    return strategy


def _quality_failed(quality_checks: list[QualityCheckResult]) -> bool:
    return any(check.status == "failed" for check in quality_checks)


def parse_agent_response(raw: str, *, run_id: str, agent: str) -> AgentResult:
    payload = _extract_json(raw)
    _validate_payload(payload)
    if payload["run_id"] != run_id:
        raise AgentRunIdMismatchError("run_id does not match")
    if payload["agent"] != agent:
        raise AgentSchemaError("agent does not match")
    if payload["provider"] != "github-copilot-cli":
        raise AgentSchemaError("provider does not match")
    findings = [_parse_finding(item) for item in payload.get("findings", [])]
    return AgentResult(
        run_id=payload["run_id"],
        agent=payload["agent"],
        provider=payload["provider"],
        schema_version=payload["schema_version"],
        decision=payload["decision"],
        findings=findings,
        summary=payload["summary"],
        status=payload.get("status", "completed"),
        reviewer_states=payload.get("reviewer_states"),
        conflicts=payload.get("conflicts"),
        incomplete_review=payload.get("incomplete_review"),
    )


def parse_subagent_final_response(raw: str, *, run_id: str) -> AgentResult:
    result = parse_agent_response(raw, run_id=run_id, agent="final")
    _validate_subagent_reviewer_states(result.reviewer_states)
    return result


def _run_agent_with_retry(client: CopilotClient, prompt: str, request: EngineRequest, agent: str) -> AgentResult:
    last_error: AgentSchemaError | AgentRunIdMismatchError | None = None
    for attempt in range(2):
        raw = client.run_prompt(prompt, timeout_seconds=request.timeout_seconds)
        try:
            return parse_agent_response(raw, run_id=request.run_id, agent=agent)
        except AgentRunIdMismatchError:
            raise
        except AgentSchemaError as exc:
            last_error = exc
            prompt += "\n\nPrevious response did not match the AgentResult schema. Return only one JSON object."
            if attempt == 1:
                raise
    raise last_error or AgentSchemaError("schema validation failed")


def new_run_id() -> str:
    return str(uuid.uuid4())


def _extract_json(raw: str) -> dict:
    if not raw.strip():
        raise AgentSchemaError("Copilot output was empty")
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise AgentSchemaError("could not extract JSON object from Copilot output")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AgentSchemaError("Copilot output JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise AgentSchemaError("Copilot output JSON must be an object")
    return payload


def _validate_payload(payload: dict) -> None:
    allowed_top_level = {
        "run_id",
        "agent",
        "provider",
        "schema_version",
        "status",
        "decision",
        "findings",
        "summary",
        "reviewer_states",
        "conflicts",
        "incomplete_review",
    }
    unknown = set(payload) - allowed_top_level
    if unknown:
        raise AgentSchemaError(f"unknown top-level fields: {', '.join(sorted(unknown))}")
    required = {"run_id", "agent", "provider", "schema_version", "decision", "findings", "summary"}
    missing = required - payload.keys()
    if missing:
        raise AgentSchemaError(f"missing required fields: {', '.join(sorted(missing))}")
    if payload["decision"] not in {"APPROVE", "APPROVE_WITH_NOTES", "CHANGES_REQUIRED", "BLOCKED", "INCONCLUSIVE"}:
        raise AgentSchemaError("decision is invalid")
    if not isinstance(payload["findings"], list):
        raise AgentSchemaError("findings must be an array")
    if "status" in payload and payload["status"] not in {"completed", "inconclusive", "blocked", "failed"}:
        raise AgentSchemaError("status is invalid")
    if "reviewer_states" in payload and payload["reviewer_states"] is not None and not isinstance(
        payload["reviewer_states"], dict
    ):
        raise AgentSchemaError("reviewer_states must be an object or null")
    if "conflicts" in payload and payload["conflicts"] is not None and not isinstance(payload["conflicts"], list):
        raise AgentSchemaError("conflicts must be an array or null")
    if "incomplete_review" in payload and payload["incomplete_review"] is not None and not isinstance(
        payload["incomplete_review"], bool
    ):
        raise AgentSchemaError("incomplete_review must be a boolean or null")
    for finding in payload["findings"]:
        if not isinstance(finding, dict):
            raise AgentSchemaError("finding must be an object")
        if finding.get("severity") not in {"Critical", "Major", "Minor", "Info"}:
            raise AgentSchemaError("finding severity is invalid")
        if not isinstance(finding.get("message"), str) or not finding.get("message", "").strip():
            raise AgentSchemaError("finding message is invalid")


def _parse_finding(item: dict) -> Finding:
    normalized = dict(item)
    if "line/range" in normalized and "line_range" not in normalized:
        normalized["line_range"] = normalized.pop("line/range")
    allowed = set(Finding.__dataclass_fields__)
    return Finding(**{key: value for key, value in normalized.items() if key in allowed})


def _build_prompt(request: EngineRequest, agent: str, previous: list[AgentResult]) -> str:
    prompt_path = Path(__file__).resolve().parent.parent / "agents" / f"{agent}.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    payload = _build_final_prompt_payload(request, previous) if agent == "final" else _build_agent_prompt_payload(
        request, agent
    )
    return prompt + "\n\nReturn only one JSON object.\nPAYLOAD_JSON\n" + _json_dumps_payload(payload)


def _build_agent_prompt_payload(request: EngineRequest, agent: str) -> dict[str, object]:
    return {
        "run_id": request.run_id,
        "agent": agent,
        "target": request.target,
        "project_id": request.repository.project_id,
        "changed_file_count": request.diff.changed_file_count,
        "diff_line_count": request.diff.diff_line_count,
        "truncated": request.diff.truncated,
        "quality_checks": [asdict(item) for item in request.quality_checks],
        "diff": request.diff.diff_text,
    }


def _build_orchestrator_prompt(request: EngineRequest) -> str:
    prompt_path = Path(__file__).resolve().parent.parent / ".github" / "agents" / "review-orchestrator.agent.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    payload = _build_orchestrator_payload(request)
    return prompt + "\n\nReturn only the Final Reviewer AgentResult JSON object.\nPAYLOAD_JSON\n" + _json_dumps_payload(
        payload
    )


def _build_orchestrator_payload(request: EngineRequest) -> dict[str, object]:
    return {
        "run_id": request.run_id,
        "execution_mode": "subagent",
        "review_target": request.target,
        "requested_legacy_agent": request.agent,
        "repository": {
            "project_id": request.repository.project_id,
            "root": str(request.repository.root),
            "remote_url": request.repository.remote_url,
            "current_branch": request.repository.current_branch,
            "head_sha": request.repository.head_sha,
        },
        "base_ref": request.repository.base_branch,
        "head_ref": request.repository.current_branch,
        "changed_files": {
            "files": request.diff.changed_files,
            "changed_file_count": request.diff.changed_file_count,
            "diff_line_count": request.diff.diff_line_count,
        },
        "review_scope": "all specialist reviewers through Review Orchestrator",
        "constraints": {
            "python_responsibility": "Prepare, validate, decide, persist",
            "copilot_responsibility": "Review and synthesize through custom subagents",
            "ai_output_is_untrusted_input": True,
            "no_previous_results_for_specialists": True,
        },
        "truncation_status": "truncated" if request.diff.truncated else "complete",
        "truncation_reason": request.diff.truncation_reason,
        "secret_scan_status": "passed",
        "quality_check_status": [
            {
                "name": check.name,
                "status": check.status,
                "returncode": check.returncode,
            }
            for check in request.quality_checks
        ],
        "common_output_schema": "schemas/agent-result.schema.json",
        "diff": request.diff.diff_text,
    }


def _build_final_prompt_payload(request: EngineRequest, previous: list[AgentResult]) -> dict[str, object]:
    specialist_results = [asdict(item) for item in previous if item.agent != "final"]
    reviewer_states = {item.agent: item.status for item in previous if item.agent != "final"}
    quality_status = [
        {
            "name": check.name,
            "status": check.status,
            "returncode": check.returncode,
        }
        for check in request.quality_checks
    ]
    return {
        "run_id": request.run_id,
        "agent": "final",
        "review_target": request.target,
        "repository": {
            "project_id": request.repository.project_id,
            "root": str(request.repository.root),
            "remote_url": request.repository.remote_url,
            "current_branch": request.repository.current_branch,
            "head_sha": request.repository.head_sha,
        },
        "base_ref": request.repository.base_branch,
        "head_ref": request.repository.current_branch,
        "changed_files": {
            "changed_file_count": request.diff.changed_file_count,
            "diff_line_count": request.diff.diff_line_count,
        },
        "truncation_status": "truncated" if request.diff.truncated else "complete",
        "truncation_reason": request.diff.truncation_reason,
        "secret_scan_status": "passed",
        "quality_check_status": quality_status,
        "specialist_results": specialist_results,
        "reviewer_states": reviewer_states,
    }


def _failed_result(run_id: str, agent: str, reason: str) -> AgentResult:
    return AgentResult(
        run_id=run_id,
        agent=agent,
        provider="github-copilot-cli",
        schema_version="0.1.0",
        decision="INCONCLUSIVE",
        findings=[Finding(severity="Info", message=reason)],
        summary=reason,
        status="failed",
    )


def _json_dumps_payload(payload: object) -> str:
    return json.dumps(_to_json_serializable(payload), ensure_ascii=False)


def _to_json_serializable(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return _to_json_serializable(value.value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _to_json_serializable(asdict(value))
    if isinstance(value, list | tuple):
        return [_to_json_serializable(item) for item in value]
    if isinstance(value, dict):
        return {
            _to_json_dict_key(key): _to_json_serializable(item)
            for key, item in value.items()
        }
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _to_json_dict_key(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str | int | float | bool) or value is None:
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} cannot be used as a JSON object key")


def _with_timing(
    result: EngineResult,
    *,
    started_at: str,
    started_monotonic: float,
    agent_durations_ms: dict[str, int],
    orchestrator_duration_ms: int | None,
    final_reviewer_duration_ms: int | None,
    execution_strategy: str,
    requested_execution_strategy: str,
) -> EngineResult:
    return EngineResult(
        run_id=result.run_id,
        provider=result.provider,
        agent_states=result.agent_states,
        agent_results=result.agent_results,
        final_decision=result.final_decision,
        max_concurrent_copilot_processes=result.max_concurrent_copilot_processes,
        execution_mode=result.execution_mode,
        execution_strategy=execution_strategy,
        requested_execution_strategy=requested_execution_strategy,
        started_at=started_at,
        finished_at=_now_iso(),
        duration_ms=_elapsed_ms(started_monotonic),
        agent_durations_ms=agent_durations_ms,
        orchestrator_duration_ms=orchestrator_duration_ms,
        final_reviewer_duration_ms=final_reviewer_duration_ms,
    )


def _raise_if_cancelled(request: EngineRequest, states: dict[str, str], agent: str) -> None:
    if _is_cancelled(request.cancel_file):
        states[agent] = "cancelled"
        raise AgentCancelledError(f"review was cancelled: {request.run_id}")


def _normalize_reviewer_states(reviewer_states: dict[str, str] | None) -> dict[str, str]:
    if not reviewer_states:
        return {}
    normalized: dict[str, str] = {}
    for agent, state in reviewer_states.items():
        key = _canonical_agent_key(agent)
        if key in AGENT_ORDER and isinstance(state, str):
            normalized[key] = state
    return normalized


def _validate_subagent_reviewer_states(reviewer_states: dict[str, str] | None) -> None:
    if not reviewer_states:
        raise AgentSchemaError("subagent final result must include reviewer_states")

    normalized: dict[str, str] = {}
    unknown_reviewers: list[str] = []
    invalid_states: list[str] = []
    for reviewer, state in reviewer_states.items():
        key = _canonical_agent_key(reviewer)
        if key not in SPECIALIST_AGENTS:
            unknown_reviewers.append(reviewer)
            continue
        if not isinstance(state, str) or state not in VALID_REVIEWER_STATES:
            invalid_states.append(f"{reviewer}={state!r}")
            continue
        normalized[key] = state

    missing = set(SPECIALIST_AGENTS) - set(normalized)
    if unknown_reviewers:
        raise AgentSchemaError(f"unknown reviewer_states reviewers: {', '.join(sorted(unknown_reviewers))}")
    if invalid_states:
        raise AgentSchemaError(f"invalid reviewer_states values: {', '.join(sorted(invalid_states))}")
    if missing:
        raise AgentSchemaError(f"missing reviewer_states reviewers: {', '.join(sorted(missing))}")


def _canonical_agent_key(value: str) -> str:
    lowered = value.strip().lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "requirements_reviewer": "requirements",
        "correctness_reviewer": "correctness",
        "security_reviewer": "security",
        "testing_reviewer": "testing",
        "maintainability_reviewer": "maintainability",
        "performance_reviewer": "performance",
        "operations_reviewer": "operations",
        "devil_advocate": "devil_advocate",
        "final_reviewer": "final",
    }
    return mapping.get(lowered, lowered)


def _has_incomplete_reviewer_state(states: dict[str, str]) -> bool:
    return any(state in INCOMPLETE_REVIEWER_STATES for agent, state in states.items() if agent != "final")


def _is_cancelled(cancel_file: Path | None) -> bool:
    return bool(cancel_file and cancel_file.exists())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
