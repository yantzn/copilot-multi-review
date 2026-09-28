# Architecture

## Overview

copilot-multi-reviewは、User Custom AgentとPython MCP Toolを組み合わせた、レビュー対象リポジトリから分離されたコードレビュー基盤です。

標準の利用者入口はVS Code GitHub Copilot Chatの `Review Orchestrator` です。Custom Agent群は `~/.copilot/agents`、MCP serverは `~/.copilot/mcp-config.json` へUser単位で登録します。レビュー対象Repositoryにはcopilot-multi-review固有ファイルを追加しません。

標準Chat経路の責務は次のように分離します。

- Python MCP `prepare_review`: 事実収集、安全前処理、Project Context収集
- Copilot Custom Agents: 意味判断、専門レビュー、反証、統合
- Python MCP `finalize_review`: contract検証、決定論的安全側判定、日本語Markdown生成
- Human: 指摘採否、修正方針、PR承認などの最終判断

既存Python Review Controller / CLI / reports / runtimeはlegacy互換経路として残します。

## Standard Chat Flow

```mermaid
flowchart TD
  A["Target repository in VS Code"] --> B["Copilot Chat / Review Orchestrator"]
  B --> C["MCP prepare_review"]
  C --> D["Resolve repository + collect git diff"]
  D --> E["Secret Scan + Project Context + Quality Check"]
  E --> F{"confirmed secret?"}
  F -->|yes| G["BLOCKED / diff is not sent to AI"]
  F -->|no| H["Independent specialist reviewers"]
  H --> I["Devil Advocate"]
  I --> J["Final Reviewer"]
  J --> K["MCP finalize_review"]
  K --> L["Schema/contract validation + deterministic safer decision"]
  L --> M["Japanese review.md outside target repository"]
  M --> N["Chat summary + human final decision"]
```

## Legacy Controller Flow

```mermaid
flowchart TD
  A["CLI or VS Code launch"] --> B["Resolve repository"]
  B --> C["Collect git diff and context"]
  C --> D["Run quality checks"]
  D --> E["Scan secrets"]
  E --> F{"confirmed secret?"}
  F -->|yes| G["BLOCKED without Copilot call"]
  F -->|no| H["Acquire project lock"]
  H --> I["Build sanitized review context"]
  I --> O["Copilot Review Orchestrator / Subagents"]
  O --> V["Validate Final Reviewer AgentResult"]
  V --> R["Python deterministic decision"]
  R --> J["Save reports/history/latest"]
  J --> K["Release own lock"]
```

## Git handling

Repository resolution uses:

- `git rev-parse --show-toplevel`
- `git rev-parse --git-common-dir`
- `git config --get remote.origin.url`
- `git branch --show-current`
- `git rev-parse HEAD`

Base branch priority:

1. CLI `--base-branch`
2. `origin/HEAD`
3. `main`
4. `develop`

No automatic fetch is performed.

## Review Controller and Agents

The standard execution mode is `subagent`.

Python acts as the Review Controller:

1. resolve the repository and review target
2. collect diff and context
3. run quality checks
4. scan secrets before any Copilot invocation
5. enforce preflight blocking
6. manage run ID, runtime files, locks, cancellation, and timeouts where Python can control them
7. build sanitized review context
8. invoke the Copilot Review Orchestrator once
9. validate the untrusted Final Reviewer `AgentResult`
10. reconcile AI and rule-based decisions with the safer result
11. persist `reports/`, `history/`, and `latest`

Copilot Custom Agentsはレビューと統合を担当します。

- Review Orchestratorは委譲だけを担当します。
- 一次専門レビュー担当は互いに独立して分析します。
- 一次専門レビュー担当には `previous_results`、`prior_findings`、`other_reviewer_results` など、他Reviewerの状態を渡しません。
- Devil Advocateは一次レビュー後に実行し、一次指摘を受け取って反証します。
- Final Reviewerは一次専門レビュー結果とDevil Advocateの反証結果の両方を受け取ります。
- Final Reviewerが返すのはAI側の判定候補であり、最終権限ではありません。

AI出力は信頼済み入力として扱いません。Python側で判定・保存前に必ず検証します。

Deprecated `legacy` mode keeps the old Python-driven serial runner temporarily:

1. requirements
2. correctness
3. design_conformance
4. project_rules
5. security
6. testing
7. maintainability
8. performance
9. operations
10. devil_advocate
11. final

Legacy mode is not a co-equal standard implementation. It exists only for migration compatibility through `--execution-mode legacy`. Removal is allowed after downstream CLI users no longer need Python to invoke `agents/*.md`; the responsibilities removed will be specialist AI invocation, reviewer result handoff, and Python-side Final Reviewer invocation.

## Persistence

`run.json` stores repository metadata, target, request, diff size, quality check summaries, agent states, Copilot CLI version, run ID, and timestamps. It does not store complete prompts, unchecked diffs, or secret values.

Locks are acquired with exclusive file creation and released only when owner and generation match.

## Copilot Custom Agent Review Orchestration

Issue #27 makes the Copilot Custom Agent orchestration the standard AI review path. Python remains the deterministic safety boundary and persistence controller.

The Custom Agent path is for human-facing orchestration in Copilot Chat:

```mermaid
flowchart TD
  U["User"] --> C["VS Code Copilot Chat"]
  C --> O["Review Orchestrator"]
  O --> R["Requirements Reviewer"]
  O --> K["Correctness Reviewer"]
  O --> S["Security Reviewer"]
  O --> T["Testing Reviewer"]
  O --> M["Maintainability Reviewer"]
  O --> P["Performance Reviewer"]
  O --> OP["Operations Reviewer"]
  O --> D["Devil Advocate Reviewer"]
  R --> F["Final Reviewer"]
  K --> F
  S --> F
  T --> F
  M --> F
  P --> F
  OP --> F
  D --> F
```

Final Reviewer synthesis and Python rule-based final decision are separate:

```mermaid
flowchart TD
  U["User"] --> O["Review Orchestrator"]
  O --> R["Requirements Reviewer"]
  O --> K["Correctness Reviewer"]
  O --> S["Security Reviewer"]
  O --> T["Testing Reviewer"]
  O --> M["Maintainability Reviewer"]
  O --> P["Performance Reviewer"]
  O --> OP["Operations Reviewer"]
  O --> D["Devil Advocate"]
  R --> IR["independent results"]
  K --> IR
  S --> IR
  T --> IR
  M --> IR
  P --> IR
  OP --> IR
  D --> IR
  IR --> F["Final Reviewer"]
  F --> AI["AI synthesis decision"]
  AI --> PY["Python rule-based decision"]
  PY --> SD["safer / stricter final decision"]
```

Only the Final Reviewer sees all specialist reviewer results. Specialist reviewers receive the same primary target context and must not receive other reviewers' findings, summaries, severities, or conclusions. The Final Reviewer receives specialist results, reviewer states, truncation status, scan/check status, and minimal target metadata for integration; it must not redo detailed specialist review from the original diff.

The current automated local review flow is:

```mermaid
flowchart TD
  A["VS Code launch / CLI"] --> B["Python Review Controller"]
  B --> C["safe sanitized context"]
  C --> D["Copilot Review Orchestrator"]
  D --> E["Specialist Subagents"]
  E --> F["Final Reviewer"]
  F --> G["Python validation / deterministic decision / persistence"]
```

### Python Review Controller Responsibilities

The Python side remains responsible for:

- git diff collection
- target repository resolution
- base/head resolution
- secret detection
- quality checks
- execution locks
- cancellation
- runtime management
- report and history persistence
- CLI commands
- VS Code launch integration
- Copilot CLI process startup for the orchestrator
- AgentResult schema validation
- fail-safe reconciliation
- safety constraints for local execution

The Python Review Controller is not a specialist reviewer. It prepares, validates, decides, and persists.

### Copilot Custom Agent Responsibilities

The Custom Agent side is responsible for:

- human-facing review entry through VS Code Copilot Chat
- Review Orchestrator behavior
- specialist reviewer selection
- delegation to reviewer subagents when those subagents exist
- collection and tracking of reviewer results
- delegation of final synthesis to the Final Reviewer when available
- explanation of agent execution state to the user

Review Orchestrator自身は、requirements、correctness、design-conformance、project-rules、security、testing、maintainability、performance、operations、devil-advocate、最終判定の詳細レビューを行いません。専門Reviewerの処理を調整し、missing / failed / blocked / inconclusive の状態を明示します。

The Final Reviewer is a read-only leaf Custom Agent. It performs synthesis only:

- merge duplicate specialist findings without overcounting
- retain `reported_by`, `reported_severities`, and Critical/Major rationale provenance
- resolve severity conflicts conservatively with `Critical > Major > Minor > Info`
- surface contradictions between reviewers under `conflicts`
- surface failed, missing, skipped, not_run, blocked, and inconclusive reviewers
- avoid unconditional `APPROVE` when context is truncated, quality/secret checks are unreliable, or required reviewers are incomplete
- emit only the existing decision vocabulary: `APPROVE`, `APPROVE_WITH_NOTES`, `CHANGES_REQUIRED`, `BLOCKED`, `INCONCLUSIVE`

The Final Reviewer decision is an AI synthesis candidate. It is not the final pass/fail authority by itself. The Python ReviewEngine keeps its existing safety rule:

```python
ai_decision = ...
rules = rule_based_decision(...)
final_decision = stricter_decision(rules, ai_decision)
```

This preserves the safer decision when AI synthesis and rule-based decision disagree.

### Boundary Rules

The design forbids:

- deleting the Python ReviewEngine
- moving the entire CLI review flow into Custom Agents
- implementing duplicate git diff collection in both Python and Custom Agents
- implementing duplicate secret scanning in both Python and Custom Agents
- adding independent git mutation logic to the Orchestrator
- allowing the Orchestrator to commit, push, merge, reset, checkout, clean, rebase, or tag
- allowing automatic fixes or generated code application from the Orchestrator
- writing review result files into the target repository

Custom Agents may receive diff/context that was already collected by the user or Python tooling, but they must not hide truncation, secret scan failures, quality check failures, missing context, failed reviewers, or unrun reviewers.

### Custom Agent Validation

Custom Agent validation has two layers:

- Custom Agent schema validation checks whether `.agent.md` frontmatter is valid YAML and structurally usable by GitHub / VS Code Custom Agents.
- Review-only policy validation checks whether this repository's review agents satisfy the local safety requirements.

An omitted `tools` field is not a general GitHub / VS Code Custom Agent schema error. For `copilot-multi-review` review-only agents, however, `tools` must be explicitly declared so the validator can verify that editing and terminal capabilities are not enabled.

Issue #28 adds UX validation for Copilot Chat subagent visibility. `Review Orchestrator` remains user-selectable. Specialist reviewers and `Final Reviewer` use the officially documented `user-invocable: false` frontmatter field so they do not appear as normal picker entries while remaining available as subagents. They must not set `disable-model-invocation: true`, because that would block ordinary subagent invocation. The Orchestrator uses `tools: ['search/codebase', 'search/usages', 'web/fetch', 'agent']` and an explicit `agents:` list whose names must match the leaf agent `name` fields exactly.

Copilot Chat progress is not implemented by this repository. Users inspect the standard VS Code / GitHub Copilot subagent tool calls for running, completed, failed, prompt/context, tool usage, and result details. See `docs/copilot-chat-review-ux.md` for the operating procedure, product-version assumptions, manual Windows E2E record, and `run_id` notes.

### Specialist Reviewer Boundaries

Issue #25 adds eight read-only Custom Agent specialist reviewers. They are leaf subagents: the Review Orchestrator may invoke them, but they must not invoke other agents, edit files, run terminal commands, or write review artifacts into the target repository. Each reviewer receives the same primary diff/context and evaluates it independently. Specialist reviewers do not receive `previous_findings`; reviewer results are aggregated only after specialist execution and then passed to the Final Reviewer.

| Reviewer | Primary responsibility | Should report | Should defer to |
| --- | --- | --- | --- |
| `requirements` / `Requirements Reviewer` | Requirements, Issue text, acceptance criteria, user request, README and architecture alignment | Requirement gaps, acceptance criteria misses, scope drift, compatibility violations | implementation logic details, security mechanics, test quality |
| `correctness` / `Correctness Reviewer` | Implementation logic, data flow, state transitions, edge cases, error handling | Bugs, invalid states, broken error propagation, incorrect API use | requirement interpretation, security impact, test adequacy |
| `security` / `Security Reviewer` | Auth, authorization, secrets, command execution, injection, path traversal, unsafe subprocess and GitHub Actions risks | Secret leakage, unsafe command execution, permission flaws, unsafe git or repository mutation | general maintainability, pure correctness bugs, test coverage |
| `testing` / `Testing Reviewer` | Meaningful test coverage for changed behavior, abnormal paths, boundary cases, regressions, and mocks | Specific untested behavior and the failure it would miss | whether requirements are correct, whether implementation logic is defective |
| `maintainability` / `Maintainability Reviewer` | Responsibility boundaries, duplication, readability, naming, cohesion, coupling, extension cost | Issues that materially raise future change or understanding cost | pure requirements, security, correctness, performance, or operations findings |
| `performance` / `Performance Reviewer` | Meaningful runtime, I/O, memory, scaling, large-diff, repeated work, and subprocess cost | Concrete stability, latency, memory, or scalability risks | style preferences, ordinary maintainability, pure correctness |
| `operations` / `Operations Reviewer` | Runtime behavior, diagnostics, locks, cancellation, rerun, Windows/Linux differences, CLI UX, installation | Recovery, observability, lock cleanup, encoding, Copilot CLI detection, and configuration risks | implementation logic, security vulnerabilities, test coverage |
| `devil_advocate` / `Devil Advocate` | Independent challenge of assumptions, fail-open behavior, surprising user paths, migration and compatibility risks | Plausible hidden assumptions or feature interaction risks | critiquing other reviewers, final synthesis, detailed specialist findings |

## Issue #29 Evaluation Architecture

The standard strategy remains `native` Copilot Subagent delegation unless real evaluation data proves that a safer alternative is better. `native` means the Review Orchestrator delegates through the standard Copilot Subagent mechanism; it does not claim that every reviewer is guaranteed to run in parallel.

The Python controller supports evaluation/helper strategies:

- `sequential`: run selected specialist reviewers one at a time, then Final Reviewer
- `limited_parallel`: run independent specialists with `max_parallel_reviewers`, then Final Reviewer
- `native`: standard Copilot Subagent delegation through `execution_mode = subagent`. The Python controller invokes the Review Orchestrator once and records `execution_strategy = native`.
- `legacy` helper with `orchestration_strategy = native`: compatibility request only. Because legacy cannot execute native Chat delegation, reports store `requested_execution_strategy = native` and `execution_strategy = sequential`.

Specialist reviewer independence is mandatory for every strategy. Specialist prompts do not include `previous_results`, other reviewer findings, shared mutable state, or early-completed reviewer output. Final Reviewer is the only component that receives specialist results, and it runs only after every selected specialist has completed or failed.

Timing fields are additive and backward compatible:

- `run.json.execution_strategy`
- `run.json.requested_execution_strategy`
- `run.json.duration_ms`
- `run.json.orchestrator_duration_ms`
- `run.json.agent_durations_ms`
- `run.json.final_reviewer_duration_ms`
- `final.json.execution_strategy`
- `final.json.requested_execution_strategy`
- `final.json.incomplete_review`

Evaluation schema and results are documented in `docs/subagent-evaluation.md` and `docs/subagent-evaluation-results-2026-08-10.json`.


## プロジェクト適合用contextの収集

Python Review Controllerは、Orchestratorを呼び出す前に、明示されたプロジェクト内contextを収集します。

- 要件: `requirements/**`、`docs/requirements.md`、README
- プロジェクトルール: `project-rules/**`、`AGENTS.md`、`.github/copilot-instructions.md`
- 設計資料: `design/**` または `docs/**` 配下の対応テキストファイルと `.xlsx`

`.xlsx` はopenpyxlで読み取り専用解析します。抽出した設計情報にはsource file、sheet、cell座標を保持します。図形、画像、色、罫線などの視覚的意味は推測しません。未対応の `.xls` とPDFは、理解済みとして扱わず警告として表面化します。

想定するレビューフロー:

```text
Python Review Controller
  -> Review Orchestrator
  -> 独立した一次レビュー担当
  -> Devil Advocateによる反証レビュー
  -> Final Reviewerによる総合整理
  -> Pythonによる決定論的な安全側統合
  -> 人間の最終判断
```
