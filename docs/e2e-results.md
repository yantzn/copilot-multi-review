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

## Issue #28 Windows Copilot Chat Subagent UX

Date: 2026-08-10

Environment:

- OS: Windows
- VS Code: manual check required; record exact version when executed
- GitHub Copilot extension: manual check required; record exact version when executed
- GitHub Copilot Chat / Agent mode: required

Manual E2E status:

| Scenario | Result | Notes |
| --- | --- | --- |
| Select `Review Orchestrator` from Copilot Chat agent picker | Documented | `Review Orchestrator` is the intended user-facing picker entry |
| Specialist reviewers hidden from normal picker | Static Passed / Manual Required | `.github/agents/*-reviewer.agent.md` and `final-reviewer.agent.md` use `user-invocable: false`; confirm local VS Code picker state manually |
| Specialist subagent tool calls visible in Chat | Manual Required | Expand standard Copilot Chat subagent tool calls; do not depend on exact UI labels |
| Subagent prompt/context/result visible | Manual Required | Confirm expanded tool call exposes the prompt/context and returned result in the installed VS Code/Copilot version |
| `Final Reviewer` invoked as subagent | Static Passed / Manual Required | Orchestrator lists `Final Reviewer` and instructs `agent` tool delegation |
| Failed/blocked/inconclusive/missing reviewer state is distinguishable | Static Passed / Manual Required | Orchestrator contract forbids treating failed or missing reviewers as success |

Representative manual steps are maintained in `docs/copilot-chat-review-ux.md`.
