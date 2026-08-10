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
- VS Code: 1.132.0, commit `df53daabb18cd157bdb08c7f01c34df936cf12f4`, x64
- GitHub Copilot extension: not installed in this Windows VS Code profile
- GitHub Copilot Chat / Agent mode: BLOCKED because the GitHub Copilot extension is not installed

Evidence:

- `code --version` returned VS Code `1.132.0`.
- `code --list-extensions --show-versions` did not list `GitHub.copilot` or `GitHub.copilot-chat`.
- The only installed extension matching `copilot` was `ms-azuretools.vscode-azure-github-copilot@1.0.230`, which is not GitHub Copilot Chat.

Manual E2E status:

| Scenario | Result | Notes |
| --- | --- | --- |
| Select `Review Orchestrator` from Copilot Chat agent picker | BLOCKED | Copilot Chat agent picker is unavailable without GitHub Copilot Chat |
| Specialist reviewers hidden from normal picker | BLOCKED / Static Passed | Runtime picker check is blocked; `.github/agents/*-reviewer.agent.md` and `final-reviewer.agent.md` use `user-invocable: false` |
| Specialist subagent tool calls visible in Chat | BLOCKED | Requires Copilot Chat subagent execution |
| Subagent prompt/context/result visible | BLOCKED | Requires expandable Copilot Chat subagent tool call details |
| `Final Reviewer` invoked as subagent | BLOCKED / Static Passed | Runtime check is blocked; Orchestrator lists `Final Reviewer` and instructs `agent` tool delegation |
| Failed/blocked/inconclusive/missing reviewer state is distinguishable | BLOCKED / Static Passed | Runtime UI check is blocked; Orchestrator contract forbids treating failed or missing reviewers as success |

Conclusion:

- The representative Windows E2E was attempted and recorded on 2026-08-10.
- Runtime validation is blocked by missing GitHub Copilot Chat in the available VS Code profile.
- Static pytest coverage validates the repository-controlled parts of the UX contract.
- A Windows VS Code profile with GitHub Copilot Chat installed is still required to replace the BLOCKED runtime rows with PASS/FAIL/PARTIAL observations.

Representative manual steps and the per-check result matrix are maintained in `docs/copilot-chat-review-ux.md`.
