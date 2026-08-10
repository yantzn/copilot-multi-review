from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    orchestration_strategy: ExecutionStrategy = "sequential"
    max_parallel_reviewers: int = DEFAULT_MAX_PARALLEL_REVIEWERS


@dataclass(frozen=True)
class EngineResult:
    run_id: str
    provider: str
    agent_states: dict[str, str]
    agent_results: list[AgentResult]
    final_decision: str
    max_concurrent_copilot_processes: int
    execution_strategy: str = "sequential"
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
    client = client or CopilotClient()
    strategy = validate_execution_strategy(request.orchestration_strategy)
    actual_strategy = _actual_controller_strategy(strategy)
    max_parallel_reviewers = validate_max_parallel_reviewers(request.max_parallel_reviewers)
    started_at = _now_iso()
    started_monotonic = time.monotonic()

    secret_scan = scan_diff_for_secrets(request.diff)
    if secret_scan.blocked:
        return EngineResult(
            run_id=request.run_id,
            provider=client.provider,
            agent_states={agent: "skipped" for agent in AGENT_ORDER},
            agent_results=[],
            final_decision="BLOCKED",
            max_concurrent_copilot_processes=0,
            execution_strategy=actual_strategy,
            requested_execution_strategy=strategy,
            started_at=started_at,
            finished_at=_now_iso(),
            duration_ms=_elapsed_ms(started_monotonic),
            agent_durations_ms={},
            orchestrator_duration_ms=0,
            final_reviewer_duration_ms=None,
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

    ai_decision = results[-1].decision if results else "INCONCLUSIVE"
    rules = rule_based_decision(
        results,
        truncated=request.diff.truncated,
        failed=failed or _quality_failed(request.quality_checks),
    )
    return EngineResult(
        run_id=request.run_id,
        provider=client.provider,
        agent_states=states,
        agent_results=results,
        final_decision=stricter_decision(rules, ai_decision),
        max_concurrent_copilot_processes=max_concurrent,
        execution_strategy=actual_strategy,
        requested_execution_strategy=strategy,
        started_at=started_at,
        finished_at=_now_iso(),
        duration_ms=_elapsed_ms(started_monotonic),
        agent_durations_ms=durations,
        orchestrator_duration_ms=orchestrator_duration_ms,
        final_reviewer_duration_ms=durations.get("final"),
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
                _cancel_parallel_work(request, states, pending_agents, futures, executor)
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
                    _cancel_parallel_work(request, states, pending_agents, futures, executor)
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
    executor: ThreadPoolExecutor,
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


def _actual_controller_strategy(strategy: str) -> str:
    if strategy == "native":
        return "sequential"
    return strategy


def _quality_failed(quality_checks: list[QualityCheckResult]) -> bool:
    return any(check.status == "failed" for check in quality_checks)


def parse_agent_response(raw: str, *, run_id: str, agent: str) -> AgentResult:
    payload = _extract_json(raw)
    _validate_payload(payload)
    if payload["run_id"] != run_id:
        raise AgentRunIdMismatchError("run_id mismatch")
    if payload["agent"] != agent:
        raise AgentSchemaError("agent mismatch")
    if payload["provider"] != "github-copilot-cli":
        raise AgentSchemaError("provider mismatch")
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
            prompt += "\n\nPrevious response did not match the schema. Return only a JSON object."
            if attempt == 1:
                raise
    raise last_error or AgentSchemaError("schema validation failed")


def new_run_id() -> str:
    return str(uuid.uuid4())


def _extract_json(raw: str) -> dict:
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
    required = {"run_id", "agent", "provider", "schema_version", "decision", "findings", "summary"}
    missing = required - payload.keys()
    if missing:
        raise AgentSchemaError(f"required keys are missing: {', '.join(sorted(missing))}")
    if payload["decision"] not in {"APPROVE", "APPROVE_WITH_NOTES", "CHANGES_REQUIRED", "BLOCKED", "INCONCLUSIVE"}:
        raise AgentSchemaError("decision is invalid")
    if not isinstance(payload["findings"], list):
        raise AgentSchemaError("findings must be an array")
    if "status" in payload and payload["status"] not in {"completed", "inconclusive", "blocked", "failed"}:
        raise AgentSchemaError("status is invalid")
    if "reviewer_states" in payload and not isinstance(payload["reviewer_states"], dict):
        raise AgentSchemaError("reviewer_states must be an object")
    if "conflicts" in payload and not isinstance(payload["conflicts"], list):
        raise AgentSchemaError("conflicts must be an array")
    if "incomplete_review" in payload and not isinstance(payload["incomplete_review"], bool):
        raise AgentSchemaError("incomplete_review must be a boolean")
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
    return prompt + "\n\nReturn JSON only.\nPAYLOAD_JSON\n" + json.dumps(payload, ensure_ascii=False)


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


def _raise_if_cancelled(request: EngineRequest, states: dict[str, str], agent: str) -> None:
    if _is_cancelled(request.cancel_file):
        states[agent] = "cancelled"
        raise AgentCancelledError(f"review was cancelled: {request.run_id}")


def _is_cancelled(cancel_file: Path | None) -> bool:
    return bool(cancel_file and cancel_file.exists())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
