# Antigravity integration

## 目的

`copilot-multi-review` のレビュー設計を、GitHub Copilotの使用制限に依存せずGoogle Antigravity IDE / Antigravity 2.0から利用できるようにします。

Python側の次の処理はCopilotと共通です。

- `prepare_review`
  - Repository / base branch解決
  - Git diff収集
  - Secret Scan
  - requirements / design / project rules収集
  - allowlist済みQuality Check
- `finalize_review`
  - Final Reviewer結果のcontract検証
  - reviewer state / truncation / quality状態の安全側統合
  - `rule_based_decision(...)`
  - `stricter_decision(...)`
  - 日本語Markdown生成

AI側だけをAntigravityのCustom Agent / Subagentへ適応します。

このintegrationはAntigravity IDE / Antigravity 2.0のGlobal Plugin / Custom Agent / MCP構成を対象にします。Antigravity CLI固有のplugin manager経路はこの変更ではruntime検証対象にしません。

## Antigravity公式機能との対応

Antigravityの現行Customizationsを次のように使います。

| copilot-multi-review | Antigravity |
|---|---|
| Review Orchestrator | Custom Agent `review-orchestrator` |
| Specialist Reviewers | Custom Subagents |
| Devil Advocate | Custom Subagent |
| Final Reviewer | Custom Subagent |
| Python deterministic processing | MCP `copilotMultiReview` |
| 明示起動 | `review` Skill / Custom Agent選択 |

Custom AgentはMarkdown + YAML frontmatterです。一次Reviewerは `mainAgent: false` / `subagent: true`、Orchestratorは `mainAgent: true` / `subagent: true` とします。

Orchestratorだけが `invoke_subagent` とMCP継承を持ちます。専門Reviewerには編集・command execution・MCPを与えません。

## 配布方式

Antigravity Pluginとして配布します。

```text
ai_review/antigravity_plugin/
├── plugin.json
├── agents/
│   ├── review-orchestrator.md
│   ├── requirements-reviewer.md
│   ├── correctness-reviewer.md
│   ├── design-conformance-reviewer.md
│   ├── project-rules-reviewer.md
│   ├── security-reviewer.md
│   ├── testing-reviewer.md
│   ├── maintainability-reviewer.md
│   ├── performance-reviewer.md
│   ├── operations-reviewer.md
│   ├── devil-advocate.md
│   └── final-reviewer.md
└── skills/
    └── review/
        └── SKILL.md
```

グローバルインストール先:

```text
~/.gemini/config/plugins/copilot-multi-review/
```

MCPはAntigravityのglobal configへ登録します。

```text
~/.gemini/config/mcp_config.json
```

server idは `copilotMultiReview` です。stdioで既存 `ai_review.mcp_server` を起動します。MCP設定には `cwd: "${workspaceFolder}"` を設定しますが、レビュー対象の正しさをこの展開だけに依存させず、Orchestratorは現在のGit repository rootの絶対パスを `prepare_review.repository_path` へ渡します。`${workspaceFolder}` のruntime展開はAntigravity IDE E2Eで確認します。

## 初回セットアップ

開発checkoutを利用する場合:

```bash
python -m pip install -e .
copilot-multi-review install --platform antigravity
```

インストール後、AntigravityのCustomizationsまたはAgent Managerで次を確認します。

- Plugin `copilot-multi-review`
- Custom Agent `review-orchestrator`
- specialist subagents
- MCP server `copilotMultiReview`
- MCP tools `prepare_review` / `finalize_review`
- Skill `review`

必要に応じてAntigravityのcustomization/MCP reloadを実行します。

## 通常利用

レビュー対象RepositoryをAntigravityで開きます。`copilot-multi-review` Repositoryを開く必要はありません。

方法A:

1. Custom Agent / Agent Managerから `review-orchestrator` を選択する
2. 「mainとの差分をレビューして」と依頼する

方法B:

1. `review` Skillを明示起動する
2. 例: `/review mainとの差分をレビューして`

## 実行フロー

```text
Review Orchestrator
  ↓
copilotMultiReview.prepare_review
  ↓
Requirements Reviewer ─┐
Correctness Reviewer   │
Design Reviewer        │
Project Rules Reviewer │ 独立contextのSubagent
Security Reviewer      │
Testing Reviewer       │
Maintainability        │
Performance            │
Operations             ┘
  ↓
Devil Advocate
  ↓
Final Reviewer
  ↓
copilotMultiReview.finalize_review
  ↓
日本語summary + review.md
```

AntigravityのSubagentは親Conversationの既存履歴を継承しない独立contextで起動されます。一次Reviewerへは同じMCP一次contextを渡し、他Reviewerのfindingsを渡しません。

## 安全境界

- Review Orchestratorはファイル編集しない
- specialist / Devil Advocate / Final Reviewerはread-only
- specialistは他Agentを起動しない
- specialistはMCPを直接呼ばない
- confirmed secret時は `prepare_review` がdiffを返さず停止する
- AIの `APPROVE` を最終判定として使わない
- `finalize_review.decision` を利用者向け最終判定とする
- 対象Repositoryへレビュー成果物を書き込まない
- commit / push / merge / reset / checkout / clean / rebase / tagを行わない

正式なMarkdownはAntigravity利用時、対象Repository外の次へ保存します。

```text
~/.gemini/config/copilot-multi-review/reviews/<project-id>/review.md
```

AntigravityのMCP設定から `COPILOT_MULTI_REVIEW_OUTPUT_HOME` を渡して出力先を分離します。Copilot経路は既存の保存先を維持します。

## 更新

```bash
git pull
python -m pip install -e .
copilot-multi-review sync --platform antigravity
```

editable installではPython sourceはcheckoutを直接参照しますが、PluginはGlobal Antigravity configへコピーされるためsyncが必要です。

## 状態確認

```bash
copilot-multi-review status --platform antigravity
```

`plugin: 最新` と `MCP: 最新` を確認します。

Antigravity UI / CLI上のruntime接続状態は、Antigravity自身のMCP/Agent Managerから確認します。

## アンインストール

```bash
copilot-multi-review uninstall --platform antigravity
```

manifestで管理しているPluginとMCP server設定だけを削除します。その他のAntigravity PluginやMCP server設定は削除しません。

管理対象PluginまたはMCP設定が手動変更されている場合、安全のため自動削除を拒否します。

## Copilotとの併用

両方利用する場合:

```bash
copilot-multi-review install --platform all
copilot-multi-review sync --platform all
copilot-multi-review status --platform all
```

`--platform` 未指定時は既存互換のため `copilot` です。

## ベンチマーク上の注意

Copilot Custom AgentとAntigravity Custom Agentは同じReviewer分割・MCP safety boundaryを共有できますが、モデル・Agent runtime・Subagent schedulingは異なります。

記事用のA〜E比較では、1つの実験内でIDE/modelを混在させず、Antigravityで比較する場合はA〜EすべてAntigravityで揃えます。これにより、レビュー設計の差とplatform/model差を混同しにくくします。
