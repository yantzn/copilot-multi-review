from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import time
from typing import Callable
import uuid

from .agents import AGENT_ORDER, AgentResult, Finding, rule_based_decision, stricter_decision
from .copilot import classify_copilot_error, resolve_copilot_command
from .diff_collector import DiffSummary
from .processes import decode_output
from .quality import QualityCheckResult
from .repository import RepositoryContext
from .secrets import combine_secret_scans, scan_diff_for_secrets, scan_payload


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
    progress_callback: Callable[[dict], None] | None = None


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

    def run_prompt(self, prompt: str, *, timeout_seconds: int, cancel_requested: Callable[[], bool] | None = None) -> str:
        command = resolve_copilot_command([])
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if platform.system() == "Windows" else 0
        process = subprocess.Popen(
            command.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=platform.system() != "Windows",
            creationflags=creationflags,
        )
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        readers = [
            threading.Thread(target=_read_stream, args=(process.stdout, stdout_chunks), daemon=True),
            threading.Thread(target=_read_stream, args=(process.stderr, stderr_chunks), daemon=True),
        ]
        for reader in readers:
            reader.start()
        try:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                if cancel_requested and cancel_requested():
                    _terminate_process_tree(process)
                    raise AgentCancelledError("Copilot execution was cancelled")
                if time.monotonic() >= deadline:
                    _terminate_process_tree(process)
                    raise subprocess.TimeoutExpired(command.command, timeout_seconds)
                time.sleep(0.2)
        finally:
            for reader in readers:
                reader.join(timeout=1)
        if process.returncode != 0:
            stderr = decode_output(b"".join(stderr_chunks))
            category = classify_copilot_error(stderr)
            if category == "rate_limit":
                raise ReviewEngineError("Copilot CLI rate limit")
            if category == "auth":
                raise ReviewEngineError("Copilot CLI authentication failed")
            raise ReviewEngineError(f"Copilot CLI failed: {category}")
        return decode_output(b"".join(stdout_chunks))


def run_review_engine(request: EngineRequest, client: CopilotClient | None = None) -> EngineResult:
    client = client or CopilotClient()
    selected_agents = [request.agent] if request.agent else list(AGENT_ORDER)
    if request.agent and request.agent not in AGENT_ORDER:
        raise ReviewEngineError(f"未知のエージェントです: {request.agent}")

    initial_payload = _build_payload(request, selected_agents[0], [])
    secret_scan = combine_secret_scans(scan_diff_for_secrets(request.diff), scan_payload(initial_payload))
    if secret_scan.blocked:
        return EngineResult(
            run_id=request.run_id,
            provider=client.provider,
            agent_states={agent: "skipped" for agent in AGENT_ORDER},
            agent_results=[],
            final_decision="BLOCKED",
            max_concurrent_copilot_processes=0,
        )

    states = {agent: "pending" for agent in AGENT_ORDER}
    results: list[AgentResult] = []
    max_concurrent = 0
    current_concurrent = 0
    failed = False

    completed_agents: list[str] = []
    total_agents = len(selected_agents)
    for index, agent in enumerate(selected_agents, start=1):
        if _is_cancelled(request.cancel_file):
            states[agent] = "cancelled"
            raise AgentCancelledError(f"レビューがキャンセルされました: {request.run_id}")
        states[agent] = "running"
        _emit_progress(
            request,
            status="running",
            current_agent=agent,
            current_agent_index=index,
            total_agents=total_agents,
            completed_agents=completed_agents,
            pending_agents=selected_agents[index:],
        )
        current_concurrent += 1
        max_concurrent = max(max_concurrent, current_concurrent)
        started = time.monotonic()
        try:
            result = _run_agent_with_retry(client, request, agent, results)
            results.append(result)
            states[agent] = "completed"
            completed_agents.append(agent)
        except AgentCancelledError:
            states[agent] = "cancelled"
            raise
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
            _emit_progress(
                request,
                status="running",
                current_agent=None,
                current_agent_index=index,
                total_agents=total_agents,
                completed_agents=completed_agents,
                pending_agents=selected_agents[index:],
            )

    for agent in AGENT_ORDER:
        if agent not in selected_agents and states[agent] == "pending":
            states[agent] = "skipped"

    ai_decision = results[-1].decision if results else "INCONCLUSIVE"
    rules = rule_based_decision(results, truncated=request.diff.truncated, failed=failed)
    result = EngineResult(
        run_id=request.run_id,
        provider=client.provider,
        agent_states=states,
        agent_results=results,
        final_decision=stricter_decision(rules, ai_decision),
        max_concurrent_copilot_processes=max_concurrent,
    )
    _emit_progress(
        request,
        status="completed" if not failed else "failed",
        current_agent=None,
        current_agent_index=total_agents,
        total_agents=total_agents,
        completed_agents=completed_agents,
        pending_agents=[],
    )
    return result


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


def _run_agent_with_retry(client: CopilotClient, request: EngineRequest, agent: str, previous: list[AgentResult]) -> AgentResult:
    last_error: AgentSchemaError | AgentRunIdMismatchError | None = None
    retry_note = ""
    for attempt in range(2):
        payload = _build_payload(request, agent, previous)
        if retry_note:
            payload["retry_instruction"] = retry_note
        if scan_payload(payload).blocked:
            raise ReviewEngineError("payload secret blocked")
        prompt = _build_prompt(agent, payload)
        raw = client.run_prompt(prompt, timeout_seconds=request.timeout_seconds, cancel_requested=lambda: _is_cancelled(request.cancel_file))
        try:
            return parse_agent_response(raw, run_id=request.run_id, agent=agent)
        except AgentRunIdMismatchError:
            raise
        except AgentSchemaError as exc:
            last_error = exc
            retry_note = "前回の応答はSchemaに一致しません。JSON objectのみを返してください。"
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


def _build_prompt(agent: str, payload: dict) -> str:
    prompt_path = Path(__file__).resolve().parent.parent / "agents" / f"{agent}.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    return prompt + "\n\nJSONで回答してください。\nPAYLOAD_JSON\n" + json.dumps(payload, ensure_ascii=False)


def _build_payload(request: EngineRequest, agent: str, previous: list[AgentResult]) -> dict:
    previous_summary = [asdict(item) for item in previous[-2:]]
    return {
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


def _emit_progress(request: EngineRequest, **payload) -> None:
    if request.progress_callback is not None:
        request.progress_callback({"run_id": request.run_id, **payload})


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


def _read_stream(stream, chunks: list[bytes]) -> None:
    while True:
        chunk = stream.read(8192)
        if not chunk:
            return
        chunks.append(chunk)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False)
        time.sleep(0.5)
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False)
        return
    try:
        os.killpg(process.pid, 15)
    except ProcessLookupError:
        return
    time.sleep(0.5)
    if process.poll() is None:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            return
