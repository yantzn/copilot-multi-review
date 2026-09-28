# Copilot Chat Review UX

This repository uses VS Code GitHub Copilot Chat as the primary human-facing review UI.

## Recommended Review Method

Use `Review Orchestrator` from the Copilot Chat agent picker.

1. User Custom Agentを `copilot-multi-review install` で初回インストールする。
2. レビュー対象のリポジトリをVS Codeで開く。`copilot-multi-review` 自体を開く必要はない。
3. GitHub Copilot Chatを開く。
4. Agent pickerで `Review Orchestrator` を選択する。
5. レビュー依頼を送信する。例:

```text
Review the diff against main in this repository.
```

or:

```text
Review the changes corresponding to PR #123.
```

5. Orchestratorが `agent` toolを使って、専門ReviewerをSubagentとして呼び出すことを確認します。
6. Copilot Chat標準のSubagent tool callを展開し、利用可能な範囲でReviewer名、prompt/context、tool usage、戻り値を確認します。
7. 一次Reviewerの後に `Devil Advocate` が実行され、一次指摘を反証していることを確認します。その後、`Final Reviewer` が一次結果と反証結果の両方を統合していることを確認します。

The CLI remains available for headless and supplementary workflows. It is not the primary UI for observing subagent progress.

## レビュー結果の出力

Copilot Chatは進捗確認と短い結果確認に使い、標準Chat経路の正式なレビュー成果物はMarkdownの `review.md` として `~/.copilot/copilot-multi-review/reviews/<project-id>/review.md` に出力します。レビュー対象Repositoryには書き込みません。

`report.md` では、内部の `Critical / Major / Minor / Info` をそのまま利用者へ見せず、`致命的 / 重大 / 軽微 / 情報` として表示します。観点も内部IDを維持したまま、利用者向けには日本語へ変換します。

具体的なFindingは次を含むことを基本とします。

- ファイル
- 行番号または範囲
- 日本語の指摘
- 日本語の根拠
- 日本語の推奨対応
- AI確信度
- 報告Reviewer

一覧はMarkdown表で提示し、その後に各指摘の詳細を続けます。根拠付きでコード位置を特定できない事項は行番号を推測せず、Human Checkまたは不足情報として分離します。

## MCP Toolの表示と実行

通常のChatレビューでは、Subagent実行の前後に次のMCP Tool callが見える想定です。

1. `copilotMultiReview/prepare_review`
2. 専門ReviewerのSubagent tool calls
3. `Devil Advocate`
4. `Final Reviewer`
5. `copilotMultiReview/finalize_review`

`prepare_review` が `blocked` を返した場合、secret保護のため専門Reviewerへdiffを渡さず終了します。

`finalize_review` が返す `decision` が利用者向けの最終判定です。Final Reviewer自身の `decision` はAI統合判定候補であり、そのまま最終表示へ採用しません。

MCP serverはUser portable config `~/.copilot/mcp-config.json` の `copilotMultiReview` として登録します。stdioで起動し、workspaceをcwdとしてPython MCP serverを起動します。

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

## User Custom Agent Distribution

通常利用では、このリポジトリの `.github/agents` を直接Workspace Agentとして使うのではなく、セットアップCLIでUser Agentへ同期します。

```text
copilot-multi-review/.github/agents
        ↓ install / sync
~/.copilot/agents/copilot-multi-review-*.agent.md
        ↓
任意のレビュー対象Repository
        ↓
Copilot Chat
        ↓
Review Orchestrator
```

インストール先ファイル名には衝突回避のため `copilot-multi-review-` prefixを付けます。Agent pickerやSubagent呼び出しで使うAgent名はfrontmatterの `name` を維持します。

更新は `git pull` 後に `copilot-multi-review sync` を実行します。Python packageをeditable installしている場合、Pythonコードはcheckoutを直接参照しますが、User Agent定義はコピーされるためsyncが必要です。

## Agent Picker Visibility

The intended normal picker entry is:

- `Review Orchestrator`

The specialist agents are kept role-named but are marked as subagent-only:

- `Requirements Reviewer`
- `Correctness Reviewer`
- `Design Conformance Reviewer`
- `Project Rules Reviewer`
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

- Project-level custom agents are loaded from `.github/agents`.
- User-level custom agents can be loaded globally from `~/.copilot/agents`; this repository installs its managed Agent files there for normal use across repositories.
- `tools: ['agent']` or a tool list containing `agent` enables subagent invocation.
- `agents:` restricts the set of custom agents available to the coordinator.
- `user-invocable: false` hides an agent from the chat agent dropdown while allowing subagent or programmatic use.
- Copilot Chat shows a running subagent as an expandable tool call and may expose the subagent prompt/context, tool calls, and result.

Known constraints:

- UI labels, icons, and placement are version-dependent.
- Subagent details may be shown inline or in richer subagent chat rendering depending on VS Code settings.
- This repository cannot force a user's local VS Code dropdown customization state.
- Manual UI validation is required for the VS Code Chat surface; pytest covers static agent topology and contracts only.

## Chat MCP Versus Legacy Controller Execution

標準のChat経路ではPython MCP Toolが決定論的処理を担当します。

- `prepare_review`: diff collection、secret scanning、quality check、Project Context
- `finalize_review`: Final Reviewer contract validation、deterministic safer decision、`review.md`

Chat経路ではlegacy Controllerの `run_id`、lock、cancel、reports/history/latestを作りません。MCP内部では `prepare_review` と `finalize_review` を安全に対応付けるため、プロセス内だけの一時的な `review_context_id` を使用します。これは永続化するrun IDではありません。

既存Python Review Controllerはlegacy互換経路として残り、CLIから実行した場合だけ従来のrun ID / reports / runtimeを使用します。

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
