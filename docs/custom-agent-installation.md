# User Custom Agent運用

`copilot-multi-review` は、日常のレビュー時にこのリポジトリを開くのではなく、Custom Agent群をユーザー共通領域へインストールし、レビュー対象リポジトリからCopilot Chatで利用する運用へ移行します。

## 役割

- `copilot-multi-review` リポジトリ: Custom Agent定義とPython補助機能の正本
- `~/.copilot/agents`: VS Code / GitHub Copilotが全Workspaceから参照するUser Custom Agent
- `~/.copilot/mcp-config.json`: Python MCP server `copilotMultiReview` のUser portable設定
- レビュー対象リポジトリ: 実際にレビューするコード。copilot-multi-review固有ファイルは追加しない

User Custom Agentの配置先はGitHub Copilot / VS Codeのユーザー共通Custom Agent配置 `~/.copilot/agents` を使用します。

## 初回セットアップ

開発checkoutを利用する場合:

```bash
python -m pip install -e .
copilot-multi-review install
```

`install` は `.github/agents/*.agent.md` を正本として、管理対象ファイルを `~/.copilot/agents` へ配置します。同時に `~/.copilot/mcp-config.json` へ `copilotMultiReview` を登録します。既存の他MCP server設定は保持します。

インストール名には `copilot-multi-review-` prefixを付けます。Agentとして表示される名前は各ファイルのfrontmatterの `name` を維持します。

## 日常利用

1. レビュー対象リポジトリをVS Codeで開く
2. Copilot Chatを開く
3. Agent pickerで `Review Orchestrator` を選ぶ
4. 例: 「mainとの差分をレビューして」と依頼する
5. Review Orchestratorが `copilotMultiReview/prepare_review` を呼び、Pythonでdiff / Secret Scan / project context / Quality Checkを準備する
6. Review Orchestratorが専門ReviewerをSubagentとして呼び出す
7. Devil Advocateで一次指摘を反証する
8. Final Reviewerが最終的な指摘候補を統合する
9. Review Orchestratorが `copilotMultiReview/finalize_review` を呼び、安全側判定と `review.md` を生成する
10. Chatに短い結果を表示し、詳細Markdownの保存先を案内する

日常レビューのために `copilot-multi-review` リポジトリを開く必要はありません。

## 更新

開発checkoutを更新した場合:

```bash
git pull
copilot-multi-review sync
```

editable installではPythonコードはcheckoutを直接参照します。Custom Agentファイルはユーザー共通領域へコピーされているため、`sync` で最新定義を反映します。

状態確認:

```bash
copilot-multi-review status
```

削除:

```bash
copilot-multi-review uninstall
```

`uninstall` はmanifestに記録された本ツール管理下のAgentとMCP server設定だけを削除します。その他のUser Custom AgentやMCP serverは削除しません。MCP設定が手動変更されている場合は安全のため自動削除を拒否します。

## 正本と配布テンプレート

開発時の正本は `.github/agents` です。

wheel等で配布した場合にもAgentをインストールできるよう、同じ内容を `ai_review/agent_templates` に同梱します。テストで両者の内容が一致していることを確認します。

checkoutから実行している場合は `.github/agents` を優先し、インストール済みpackageだけの場合は同梱テンプレートを使用します。

## 安全境界

セットアップCLIは次だけを担当します。

- User Custom Agentのinstall
- sync
- status
- uninstall

日常のコードレビューをCLIから開始するための機能は追加しません。

既存のPython Review Controller / legacy CLIは移行期間中は残しますが、User Custom Agent運用の通常入口ではありません。

## Python MCPとの関係

User Custom Agentは `copilotMultiReview` MCP serverの2 Toolを使います。

### prepare_review

- Repository / base branch解決
- Git diff収集
- Secret Scan
- 要件・設計書・Project Rules収集
- allowlist済みQuality Check
- AIへ渡す構造化context生成

confirmed secret候補を検出した場合、diffはAIへ返さず `blocked` にします。

### finalize_review

- Final Reviewer結果のcontract検証
- reviewer state / truncation / quality状態の確認
- `rule_based_decision(...)`
- `stricter_decision(...)`
- 日本語Markdown `review.md` の生成

MCPはstdio transportで起動し、User portable configのserver idは `copilotMultiReview` です。設定では `cwd: "${workspaceFolder}"` を使用するため、通常は現在VS Codeで開いているRepositoryがレビュー対象になります。

詳細Markdownは次へ保存します。

```text
~/.copilot/copilot-multi-review/reviews/<project-id>/review.md
```

MCP実装後も、利用者の通常入口は `Review Orchestrator` のままです。
