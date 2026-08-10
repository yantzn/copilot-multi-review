# Operations

## Daily Review

Base diff:

```bash
ai-review review --repo <path> --target base
```

Uncommitted security-only review:

```bash
ai-review review --repo <path> --target uncommitted --agent security
```

Staged diff:

```bash
ai-review review --repo <path> --target staged
```

Commit range:

```bash
ai-review review --repo <path> --target commits --commits <from>..<to>
```

Single file:

```bash
ai-review review --repo <path> --target file --file src/app.py
```

## Results

```bash
ai-review show-latest --repo <path>
ai-review rerun --repo <path>
```

Reports are stored under:

```text
reports/<project-id>/latest/
reports/<project-id>/history/<run-id>/
```

## Cancel

```bash
ai-review cancel --repo <path>
```

With a known run ID:

```bash
ai-review cancel --repo <path> --run-id <run-id>
```

Cancel writes `runtime/<project-id>/cancel.json`. The engine checks it between agents.

## Cleanup

Dry-run:

```bash
ai-review cleanup-locks --repo <path>
```

Apply:

```bash
ai-review cleanup-locks --repo <path> --apply
```

Cleanup returns one of:

- `dry_run`
- `deleted`
- `nothing_to_delete`
- `refused`
- `partially_deleted`

## E2E Record

Validated in this development environment:

1. VS Code launch JSON structure and required configurations: automated test passed
2. Uncommitted diff Security preview path: automated preview test passed
3. Real secret blocks before Copilot: automated test passed, Copilot calls 0
4. Detector definition does not self-block: automated test passed
5. Cancel run ID guard: automated test passed
6. Double start rejection: automated lock test passed
7. Separate project latest reports: automated test passed
8. show-latest: manual CLI check passed
9. rerun: manual CLI check passed with `--no-agents`
10. Windows BAT/CMD: automated command construction tests passed

Manual GUI selection and real Copilot CLI execution were not performed because this environment is headless for UI confirmation and Copilot CLI is not installed.

## Issue #29 Subagent Evaluation Operations

Recommended standard path:

```text
VS Code -> Copilot Chat -> Agent selection -> Review Orchestrator
```

The selected standard execution strategy is `native`. This uses the standard GitHub Copilot / VS Code Subagent UI. It must not be described as guaranteed full parallel execution.

Python CLI helper paths:

```bash
ai-review review --repo <path> --target base --orchestration-strategy sequential
ai-review review --repo <path> --target base --orchestration-strategy limited_parallel --max-parallel-reviewers 2
```

Use these helper strategies for controlled comparison and deterministic report persistence. Do not promote the legacy Python nine-call sequential runner back to the standard path.

If `--orchestration-strategy native` is passed to the Python helper, the report records `requested_execution_strategy: native` and `execution_strategy: sequential`. Native delegation itself is available only through the standard Copilot Chat/Subagent path.

Evaluation artifacts:

```text
tests/fixtures/subagent_evaluation_scenarios.json
docs/subagent-evaluation.md
docs/subagent-evaluation-results-2026-08-10.json
```

Failure meanings:

- specialist failed: Final Reviewer may still run, but final decision must not be `APPROVE`
- Final Reviewer failed: final decision must not be `APPROVE`
- secret scan blocked: Copilot is not invoked and final decision is `BLOCKED`
- Chat UI unavailable: record `BLOCKED` or `NOT_OBSERVABLE`, not `PASS`
- limited parallel cancelled: the controller stops submitting new specialists and does not start Final Reviewer

Windows Japanese path check uses a path like `C:\...\レビュー対象\サンプル` and verifies repository resolution, diff collection, UTF-8 handling, subprocess-safe prompt construction, report output, and run history.
