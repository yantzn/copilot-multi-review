# Copilot Chat Review UX

This repository uses VS Code GitHub Copilot Chat as the primary human-facing review UI.

## Recommended Review Method

Use `Review Orchestrator` from the Copilot Chat agent picker.

1. Open the repository to review in VS Code.
2. Open GitHub Copilot Chat.
3. Select `Review Orchestrator` in the agent picker.
4. Send a review request, for example:

```text
Review the diff against main in this repository.
```

or:

```text
Review the changes corresponding to PR #123.
```

5. The Orchestrator uses the `agent` tool to invoke specialist reviewers as subagents.
6. Expand the standard Copilot Chat subagent tool calls to inspect the invoked reviewer name, prompt/context, tool usage when exposed by Copilot, and returned result.
7. Confirm that `Final Reviewer` ran as a subagent and integrated the specialist results.

The CLI remains available for headless and supplementary workflows. It is not the primary UI for observing subagent progress.

## Standard Subagent UI

No custom progress UI is implemented for Issue #28. The expected progress and result display is the standard VS Code / GitHub Copilot subagent UI for `agent` tool calls.

The user should be able to determine, through the standard UI:

- which subagent was started
- whether a subagent is currently running
- whether a subagent completed
- whether a subagent failed or was blocked/inconclusive according to the returned reviewer state
- whether the tool call can be expanded
- what prompt/context was passed to the subagent, when Copilot exposes that detail
- what result the subagent returned

Do not rely on exact icon names, labels, or UI strings in code or tests. VS Code and Copilot may change the presentation across versions.

## Agent Picker Visibility

The intended normal picker entry is:

- `Review Orchestrator`

The specialist agents are kept role-named but are marked as subagent-only:

- `Requirements Reviewer`
- `Correctness Reviewer`
- `Security Reviewer`
- `Testing Reviewer`
- `Maintainability Reviewer`
- `Performance Reviewer`
- `Operations Reviewer`
- `Devil Advocate`
- `Final Reviewer`

The implementation uses the officially documented `user-invocable: false` frontmatter field on these specialist agent files. According to current VS Code and GitHub Copilot documentation, this hides an agent from the chat agent dropdown while leaving it accessible as a subagent. The repository does not use guessed fields such as `hidden`, `picker visibility`, or `subagent-only`.

The specialist agents do not set `disable-model-invocation: true`, because that property prevents invocation as a subagent unless a coordinator explicitly overrides it. `Review Orchestrator` explicitly lists the specialist names in its `agents:` frontmatter and includes the `agent` tool.

Users can still customize the local VS Code agents dropdown from VS Code itself. If a local user-level or extension-contributed agent with the same or similar name exists, the visible list may differ from this repository's intent.

## Version Assumptions And Constraints

Confirmed documentation basis:

- VS Code documentation for custom agents and subagents, checked on 2026-08-10.
- GitHub Docs custom agents configuration reference, checked on 2026-08-10.

Expected product capabilities:

- Workspace custom agents are loaded from `.github/agents`.
- `tools: ['agent']` or a tool list containing `agent` enables subagent invocation.
- `agents:` restricts the set of custom agents available to the coordinator.
- `user-invocable: false` hides an agent from the chat agent dropdown while allowing subagent or programmatic use.
- Copilot Chat shows a running subagent as an expandable tool call and may expose the subagent prompt/context, tool calls, and result.

Known constraints:

- UI labels, icons, and placement are version-dependent.
- Subagent details may be shown inline or in richer subagent chat rendering depending on VS Code settings.
- This repository cannot force a user's local VS Code dropdown customization state.
- Manual UI validation is required for the VS Code Chat surface; pytest covers static agent topology and contracts only.

## Chat-only Versus Controller Execution

When the Python Review Controller runs a review, it owns deterministic safety processing:

- diff collection
- secret scanning before AI receives the diff
- quality check status
- `run_id`
- report/history/latest persistence
- schema validation
- deterministic and stricter final decision logic

When the user starts `Review Orchestrator` directly from Copilot Chat, there may be no controller-created `run_id`. In that case, the Orchestrator must not invent or persist a fake ID. The Chat transcript itself is the observable execution record.

If a Controller-originated workflow supplies a `run_id` in the context passed to `Review Orchestrator`, the Orchestrator passes that existing value through to specialists and `Final Reviewer`. This links the Chat execution to report/history data without relying on a Copilot Chat API or UI ID that is not available to this repository.

## Windows Manual E2E Record

Environment recorded for Issue #28:

- OS: Windows
- Date: 2026-08-10
- VS Code: 1.132.0, commit `df53daabb18cd157bdb08c7f01c34df936cf12f4`, x64
- GitHub Copilot extension: not installed in this Windows VS Code profile
- GitHub Copilot Chat / Agent mode: blocked because the GitHub Copilot extension is not installed
- Repository: `yantzn/copilot-multi-review`

Evidence collected:

- `code --version` returned VS Code `1.132.0`.
- `code --list-extensions --show-versions` did not list `GitHub.copilot` or `GitHub.copilot-chat`.
- The only extension name containing `copilot` was `ms-azuretools.vscode-azure-github-copilot@1.0.230`, which is not GitHub Copilot Chat.

Representative scenario result:

| Check | Result | Notes |
| --- | --- | --- |
| Review Orchestrator picker visibility | BLOCKED | Copilot Chat agent picker is unavailable without GitHub Copilot Chat. |
| Specialists hidden from picker | BLOCKED / Static PASS | Runtime picker check is blocked; static config uses `user-invocable: false` for specialist reviewers and `Final Reviewer`. |
| Specialist subagent invoked | BLOCKED | Requires Copilot Chat agent execution. |
| Agent name visible | BLOCKED | Requires Copilot Chat subagent tool call UI. |
| Tool call expandable | BLOCKED | Requires Copilot Chat subagent tool call UI. |
| Prompt/context visible | BLOCKED | Requires expanded Copilot Chat subagent tool call details. |
| Tool usage visible | BLOCKED | Requires expanded Copilot Chat subagent tool call details. |
| Result visible | BLOCKED | Requires Copilot Chat subagent execution. |
| Final Reviewer invoked as subagent | BLOCKED / Static PASS | Runtime check is blocked; `Review Orchestrator` explicitly lists `Final Reviewer` and instructs `agent` tool delegation. |
| Failure/incomplete state distinguishable | BLOCKED / Static PASS | Runtime UI check is blocked; Orchestrator contract requires `failed`, `blocked`, `inconclusive`, `missing`, `skipped`, and `not_run` to remain explicit. |

Manual steps for an environment with GitHub Copilot Chat installed:

1. Open this repository in VS Code on Windows.
2. Open GitHub Copilot Chat.
3. Select `Review Orchestrator` from the agent picker.
4. Send:

```text
Review the diff against main in this repository.
```

5. Confirm that specialist subagents are invoked through standard Copilot Chat tool calls.
6. Expand at least one specialist subagent tool call.
7. Confirm that the expanded details identify the reviewer name, the prompt/context, any visible tool usage, and the returned result.
8. Confirm that `Final Reviewer` is invoked as a subagent after specialist results are available.
9. Run a failure-oriented check by providing intentionally insufficient context, for example:

```text
Review PR #0 without repository, diff, or branch context.
```

10. Confirm that the Orchestrator marks the relevant reviewer state as `missing`, `not_run`, `blocked`, `failed`, or `inconclusive` instead of treating it as a successful review.

Expected:

- `Review Orchestrator` is the normal user-selected agent.
- Specialist reviewer names remain role-based and visible in standard Copilot Chat subagent UI.
- Specialist reviewers are not shown as normal picker choices by this repository configuration.
- Specialist reviewer results are independent.
- Only `Final Reviewer` receives specialist results.
- No custom UI, WebView, HTML reviewer dashboard, terminal spinner, or progress simulation appears.

Current E2E conclusion:

- The Windows E2E was attempted and recorded on 2026-08-10.
- Runtime Copilot Chat validation is blocked in this environment because GitHub Copilot Chat is not installed.
- Static repository checks cover the agent topology, picker visibility intent, subagent availability, and Orchestrator contract.
- A follow-up manual run on a Windows VS Code profile with GitHub Copilot Chat installed should replace the `BLOCKED` runtime rows with PASS/FAIL/PARTIAL observations from the actual UI.
