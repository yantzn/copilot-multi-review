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

Residual manual checks:

- Real VS Code GUI folder selection on a desktop session
- Real GitHub Copilot CLI prompt execution after installing and authenticating Copilot CLI
- Large real-world repository audit with authenticated Copilot CLI
