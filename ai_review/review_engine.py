from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import time
import uuid

from .agents import AGENT_ORDER, AgentResult, Finding, rule_based_decision, stricter_decision
from .copilot import classify_copilot_error, resolve_copilot_command
from .diff_collector import DiffSummary
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


@dataclass(frozen=True)
class EngineResult:
    run_id: str
    provider: str
    agent_states: dict[str, str]
    agent_results: list[AgentResult]
    final_decision: str
    max_concurrent_copilot_processes: int


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
    secret_scan = scan_diff_for_secrets(request.diff)
    if secret_scan.blocked:
        return EngineResult(
            run_id=request.run_id,
            provider=client.provider,
            agent_states={agent: "skipped" for agent in AGENT_ORDER},
            agent_results=[],
            final_decision="BLOCKED",
            max_concurrent_copilot_processes=0,
        )
    selected_agents = [request.agent] if request.agent else list(AGENT_ORDER)
    if request.agent and request.agent not in AGENT_ORDER:
        raise ReviewEngineError(f"未知のエージェントです: {request.agent}")

    states = {agent: "pending" for agent in AGENT_ORDER}
    results: list[AgentResult] = []
    max_concurrent = 0
    current_concurrent = 0
    failed = False

    for agent in selected_agents:
        if _is_cancelled(request.cancel_file):
            states[agent] = "cancelled"
            raise AgentCancelledError(f"レビューがキャンセルされました: {request.run_id}")
        states[agent] = "running"
        current_concurrent += 1
        max_concurrent = max(max_concurrent, current_concurrent)
        started = time.monotonic()
        try:
            prompt = _build_prompt(request, agent, results)
            result = _run_agent_with_retry(client, prompt, request, agent)
            results.append(result)
            states[agent] = "completed"
        except subprocess.TimeoutExpired:
            states[agent] = "failed"
            failed = True
            results.append(_failed_result(request.run_id, agent, "timeout"))
        except ReviewEngineError:
            states[agent] = "failed"
            failed = True
            results.append(_failed_result(request.run_id, agent, "failed"))
        finally:
            current_concurrent -= 1
            _ = started

    for agent in AGENT_ORDER:
        if agent not in selected_agents and states[agent] == "pending":
            states[agent] = "skipped"

    ai_decision = results[-1].decision if results else "INCONCLUSIVE"
    rules = rule_based_decision(results, truncated=request.diff.truncated, failed=failed)
    return EngineResult(
        run_id=request.run_id,
        provider=client.provider,
        agent_states=states,
        agent_results=results,
        final_decision=stricter_decision(rules, ai_decision),
        max_concurrent_copilot_processes=max_concurrent,
    )


def parse_agent_response(raw: str, *, run_id: str, agent: str) -> AgentResult:
    payload = _extract_json(raw)
    _validate_payload(payload)
    if payload["run_id"] != run_id:
        raise AgentRunIdMismatchError("run_idが一致しません。")
    if payload["agent"] != agent:
        raise AgentSchemaError("agentが一致しません。")
    if payload["provider"] != "github-copilot-cli":
        raise AgentSchemaError("providerが一致しません。")
    findings = [Finding(**item) for item in payload.get("findings", [])]
    return AgentResult(
        run_id=payload["run_id"],
        agent=payload["agent"],
        provider=payload["provider"],
        schema_version=payload["schema_version"],
        decision=payload["decision"],
        findings=findings,
        summary=payload["summary"],
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
            prompt += "\n\n前回の応答はSchemaに一致しません。JSON objectのみを返してください。"
            if attempt == 1:
                raise
    raise last_error or AgentSchemaError("Schema検証に失敗しました。")


def new_run_id() -> str:
    return str(uuid.uuid4())


def _extract_json(raw: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise AgentSchemaError("Copilot出力からJSONを抽出できません。")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AgentSchemaError("Copilot出力JSONが不正です。") from exc
    if not isinstance(payload, dict):
        raise AgentSchemaError("Copilot出力JSONはobjectである必要があります。")
    return payload


def _validate_payload(payload: dict) -> None:
    required = {"run_id", "agent", "provider", "schema_version", "decision", "findings", "summary"}
    missing = required - payload.keys()
    if missing:
        raise AgentSchemaError(f"必須キーが不足しています: {', '.join(sorted(missing))}")
    if payload["decision"] not in {"APPROVE", "APPROVE_WITH_NOTES", "CHANGES_REQUIRED", "BLOCKED", "INCONCLUSIVE"}:
        raise AgentSchemaError("decisionが不正です。")
    if not isinstance(payload["findings"], list):
        raise AgentSchemaError("findingsは配列である必要があります。")
    for finding in payload["findings"]:
        if finding.get("severity") not in {"Critical", "Major", "Minor", "Info"}:
            raise AgentSchemaError("finding severityが不正です。")


def _build_prompt(request: EngineRequest, agent: str, previous: list[AgentResult]) -> str:
    prompt_path = Path(__file__).resolve().parent.parent / "agents" / f"{agent}.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    previous_summary = [asdict(item) for item in previous[-2:]]
    payload = {
        "run_id": request.run_id,
        "agent": agent,
        "target": request.target,
        "project_id": request.repository.project_id,
        "changed_file_count": request.diff.changed_file_count,
        "diff_line_count": request.diff.diff_line_count,
        "truncated": request.diff.truncated,
        "quality_checks": [asdict(item) for item in request.quality_checks],
        "previous_results": previous_summary,
        "diff": request.diff.diff_text,
    }
    return prompt + "\n\nJSONで回答してください。\nPAYLOAD_JSON\n" + json.dumps(payload, ensure_ascii=False)


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


def _is_cancelled(cancel_file: Path | None) -> bool:
    return bool(cancel_file and cancel_file.exists())
