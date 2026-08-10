# Subagent Evaluation

Issue #29 evaluates the Copilot native Subagent review topology before changing the standard execution strategy.

## Standard Strategy

Selected standard strategy: `native`

Reason: this environment does not provide enough real Copilot Chat/Subagent, quality, credit, UI, or internal parallelism data to justify changing the default. The safe initial decision from Issue #29 is therefore to keep Copilot native delegation as the standard path. The default Python controller `execution_mode = subagent` records `execution_strategy = native`. `sequential` and `limited_parallel` remain comparison/helper strategies for `execution_mode = legacy` only. If `native` is requested through the legacy helper, reports separate the request from the actual controller behavior: `requested_execution_strategy = native`, `execution_strategy = sequential`.

Do not treat `native` as a guarantee that every reviewer runs concurrently. It means delegation is handled by the standard GitHub Copilot / VS Code Subagent mechanism.

## Compared Strategies

```text
Strategy          Quality    Duration    Credits       Rate limit
---------------------------------------------------------------
sequential        PARTIAL    pytest only  Unavailable   not observed
limited_parallel  PARTIAL    pytest only  Unavailable   not observed
native            BLOCKED    unavailable  Unavailable   not observed
```

The current automated run did not send real Copilot requests. Values that require real Copilot execution are recorded as `BLOCKED`, `NOT_OBSERVABLE`, or `Unavailable`, not inferred.

## Data Model

Evaluation records are structured JSON. The representative schema is implemented in `ai_review.evaluation` and includes:

- `scenario`
- `execution_strategy`
- `started_at`, `finished_at`, `duration_ms`
- `orchestrator_duration_ms`
- specialist reviewer durations when observable
- `final_reviewer_duration_ms`
- `expected_findings`
- `actual_findings`
- `metrics.critical_major_recall`
- `metrics.false_positive_count`
- `metrics.duplicate_finding_count`
- `credits`
- `rate_limit`
- `chat_ui`
- `final_reviewer_quality`
- `report_consistency`
- `final_decision`

Result record: `docs/subagent-evaluation-results-2026-08-10.json`

Scenario fixture: `tests/fixtures/subagent_evaluation_scenarios.json`

## Quality Metrics

Critical/Major recall is computed as:

```text
matched expected Critical/Major findings / expected Critical/Major findings
```

False positives count only actual Major/Critical findings that do not match any ground truth concept.

Duplicate findings are grouped by category, file, and normalized concept terms. This intentionally avoids embeddings and does not require exact string equality.

## Reviewer Independence

Specialist reviewers must remain independent:

- no reviewer receives another reviewer's findings
- no `previous_results` in specialist prompts
- no shared mutable prompt state
- no order dependency
- no passing early results into reviewers still running

Only the Final Reviewer receives specialist results. The Final Reviewer runs after every selected specialist has completed or failed.

For legacy `limited_parallel`, the controller keeps only `max_parallel_reviewers` specialists in flight, checks `cancel_file` while waiting for futures, stops submitting new reviewers after cancellation, and never starts Final Reviewer after cancellation. Already running Copilot subprocesses cannot be treated as stopped unless the process layer exposes a cancellable handle; this is a controller cancellation guarantee, not proof that GitHub Copilot killed an in-flight request.

## Failure Behavior

One specialist failure cannot approve. The Python deterministic rule marks the run at least `INCONCLUSIVE` even when the Final Reviewer returns `APPROVE`.

Final Reviewer failure cannot approve. Timeout, invalid schema, malformed JSON, subprocess failure, or failed status are converted to a failed Final Reviewer result and the deterministic rule prevents `APPROVE`.

Secret scan remains before Copilot invocation. Confirmed secrets produce `BLOCKED` with zero Copilot calls.

## Timing

The Python controller records:

- whole-run `duration_ms`
- orchestrator/specialist phase duration
- specialist reviewer duration for the controller path
- Final Reviewer duration

Chat-only internal timing is `NOT_OBSERVABLE` unless GitHub Copilot / VS Code exposes it during manual E2E. The project must not infer hidden Chat/Subagent timing.

## AI Credits

GitHub documents account or organization billing/usage APIs and UI-level billing information for AI credits or premium requests, but the project did not find a formal, supported interface that exposes per-subagent or per-reviewer credit usage.

Recorded value:

```text
credits.available = false
credits.reason = "Current GitHub Copilot interfaces do not expose per-subagent credit usage."
```

Do not estimate credits from tokens, prompt length, duration, or fixed conversion factors.

## Rate Limit And Throttling

The controller classifies Copilot CLI stderr that looks like rate limiting as a failure. Automated tests do not intentionally generate rate limits. If no rate limit is observed during safe normal use, record:

```json
{
  "observed": false,
  "observable": true
}
```

Use `observable = false` only when the environment cannot expose the signal at all.

## Chat UI Observability

Manual E2E must record whether the standard VS Code / GitHub Copilot Subagent UI shows:

- reviewer name
- running/completed/failed state
- prompt/context
- tool usage
- returned result

No custom web UI or VS Code extension is allowed for this observability.

## Manual E2E Scenarios

1. clean small diff
2. requirements violation
3. correctness bug
4. security vulnerability
5. missing tests
6. multi-concern diff
7. large diff
8. secret blocked before AI
9. one Subagent failure
10. Final Reviewer failure
11. Windows Japanese path, such as `C:\...\レビュー対象\サンプル`

Manual statuses must be one of `PASS`, `FAIL`, `PARTIAL`, `BLOCKED`, or `NOT_OBSERVABLE`.

## Issue #6

Issue #6 status: legacy and superseded by #23-#29 for the standard path.

Issue #6 implemented the original MVP: Python invokes nine logical Copilot reviewers sequentially with maximum concurrency 1. That issue is already closed. Its runner remains useful as a legacy/helper comparison path, but it must not become the standard again. The standard path after #23-#29 is Copilot Custom Agent / Subagent orchestration with Python retaining deterministic safety, report persistence, and fail-safe final decision handling.
