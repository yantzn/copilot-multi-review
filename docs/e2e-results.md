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
| Repository audit small repository | Passed | `ai-review audit --repo . --no-agents` generated latest/history outputs |
| Repository audit batching | Passed | automated tests cover stable batch IDs, directory/language grouping, max files, and max lines |
| Repository audit secret block | Passed | confirmed secret test keeps Copilot call count at 0 |
| Repository audit status | Passed | automated tests cover running status fields and watch terminal conditions |
| Repository audit rerun | Passed | previous audit request can be restored for full rerun |
| Repository audit worktree | Passed | automated worktree collection test passed |
| Repository audit 15,000-line file | Passed | automated test confirms three non-overlapping 5,000-line segments |
| Repository audit tracked + untracked | Passed | automated test confirms tracked flags and `--include-untracked` behavior |
| Repository audit secret file BLOCKED | Passed | `.env`, `.env.local`, `.key`, `.pem`, `.p12` block with Copilot calls 0 |
| Repository audit analysis-only | Passed | `--no-agents` records `execution_mode: analysis_only` |
| Repository audit cancelled vs failed | Passed | automated tests keep cancelled batches out of failed batches |
| Repository audit Copilot error classification | Passed | authentication/rate limit/timeout/schema/process/network/cancel/unexpected mappings tested |
| Repository audit status watch suppression | Passed | automated CLI test prints unchanged status only once |
| Secret file substring exceptions removed | Passed | `tests/production.pem`, `examples/real-private.key`, `contest/.env`, and `documentation-backup/prod.p12` block |
| Batch final payload secret scan | Passed | previous batch result token blocks the next agent before another Copilot call |
| Cross final payload secret scan | Passed | previous cross result token blocks later cross agents |
| In-flight Copilot cancel | Passed | Popen-based test cancels a running child process before timeout |
| Analysis-only expected calls zero | Passed | `--no-agents` with `max_copilot_calls=0` succeeds |
| No reviewable files | Passed | empty and binary-only repositories return INCONCLUSIVE without Copilot calls |
| Safe error messages | Passed | token, DB URL, PEM, password, and multiline exception text are not stored in error messages |

Residual manual checks:

- Real VS Code GUI folder selection on a desktop session
- Real GitHub Copilot CLI prompt execution after installing and authenticating Copilot CLI
- Large real-world repository audit with authenticated Copilot CLI
