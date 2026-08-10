# E2E Results

Date: 2026-08-01

Environment:

- OS: Windows
- Python: 3.12.13 in local `.venv`
- Copilot CLI: not installed in this environment

| Scenario | Result | Notes |
| --- | --- | --- |
| VS Code ▶ → folder selection → confirmation → all agents | Partial | launch.json structure is tested; GUI manual execution was not performed |
| Uncommitted Security single agent | Passed | preview and single-agent engine path are automated |
| Real secret blocks before Copilot | Passed | automated test confirms Copilot calls 0 |
| Detector definition does not self-block | Passed | automated test |
| cancel | Passed | run_id guard and cancel file creation tested |
| double start rejection | Passed | atomic lock test |
| report separation | Passed | two-project latest separation test |
| show-latest | Passed | manual CLI check |
| rerun | Passed | manual CLI check with `--no-agents` |
| Windows BAT/CMD | Passed | command construction and COMSPEC fallback tested |

Residual manual checks:

- Real VS Code GUI folder selection on a desktop session
- Real GitHub Copilot CLI prompt execution after installing and authenticating Copilot CLI

## Issue #29 Subagent Evaluation Record

Date: 2026-08-10

Detailed machine-readable record:

```text
docs/subagent-evaluation-results-2026-08-10.json
```

Scenario fixture:

```text
tests/fixtures/subagent_evaluation_scenarios.json
```

| Scenario | Status | Notes |
| --- | --- | --- |
| clean-small-diff | BLOCKED | Requires authenticated Copilot Chat Subagent execution |
| requirements-violation | BLOCKED | Requires authenticated Copilot Chat Subagent execution |
| correctness-bug | BLOCKED | Requires authenticated Copilot Chat Subagent execution |
| security-vulnerability | BLOCKED | Requires authenticated Copilot Chat Subagent execution |
| missing-tests | BLOCKED | Requires authenticated Copilot Chat Subagent execution |
| multi-concern | BLOCKED | Requires authenticated Copilot Chat Subagent execution |
| large-diff | BLOCKED | Requires authenticated Copilot Chat Subagent execution |
| secret-blocked-before-ai | PASS | pytest confirms zero Copilot calls before BLOCKED |
| one-subagent-failure | PASS | pytest confirms final decision is not APPROVE |
| final-reviewer-failure | PASS | pytest confirms final decision is not APPROVE |
| windows-japanese-path | PASS | pytest uses `レビュー対象\サンプル` |

Strategy comparison:

```text
Strategy          Quality    Duration    Credits       Rate limit
---------------------------------------------------------------
sequential        PARTIAL    pytest only  Unavailable   not observed
limited_parallel  PARTIAL    pytest only  Unavailable   not observed
native            BLOCKED    unavailable  Unavailable   not observed
```

Standard strategy decision: keep `native` delegation as the standard because real Copilot Subagent evidence was not sufficient to prove that `limited_parallel` improves speed without quality, independence, credit, rate limit, or complexity regressions.
