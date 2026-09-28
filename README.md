# copilot-multi-review

VS Code GitHub Copilot ChatまたはGoogle AntigravityのCustom Agent/Subagentを入口に、複数の専門Reviewerでコードレビューを行う基盤です。Python MCPの決定論的処理は両platformで共通利用します。既存のPython Review Controller / CLIは移行期間中の互換経路として残しています。

レビューエンジン、プロンプト、Schema、runtime、レポートはこの専用リポジトリへ集約します。レビュー対象の外部Gitリポジトリには、レビュー用コード、設定、レポート、runtimeを作成しません。

## 初回セットアップ

Python 3.11以上を使用してください。VS CodeでPython 3.10が選ばれる場合は、コマンドパレットの`Python: Select Interpreter`から3.11以上の仮想環境を選びます。

```bash
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e .[dev]
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .[dev]
```

通常のCustom Agent運用では、VS CodeでGitHub Copilot Chat / Agent機能を利用できる状態にしてください。

既存のlegacy CLI経路を使用する場合のみ、GitHub Copilot CLIの導入・認証と `ai-review validate-config` が必要です。

```bash
copilot version
copilot login
ai-review validate-config
```

WindowsではUTF-8環境を推奨します。

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
```

## User Custom Agentセットアップ

通常の対話レビューでは、`copilot-multi-review` リポジトリを開く必要はありません。Custom Agent群をユーザー共通の `~/.copilot/agents` へインストールし、レビュー対象リポジトリをVS Codeで開いたままCopilot Chatから利用します。

初回:

```bash
python -m pip install -e .
copilot-multi-review install
```

更新:

```bash
git pull
copilot-multi-review sync
```

状態確認:

```bash
copilot-multi-review status
```

削除:

```bash
copilot-multi-review uninstall
```

通常利用:

1. レビュー対象リポジトリをVS Codeで開く
2. Copilot Chatを開く
3. `Review Orchestrator` を選択
4. 「mainとの差分をレビューして」などと依頼する

Custom Agentの正本は `.github/agents` です。セットアップCLIは管理対象Agentだけをユーザー共通領域へ同期し、他のUser Agentは変更しません。あわせてUser MCP config `~/.copilot/mcp-config.json` に `copilotMultiReview` を登録し、既存の他MCP server設定は保持します。

詳細は `docs/custom-agent-installation.md` を参照してください。

## Antigravity integration

Google Antigravity IDE / Antigravity 2.0向けには、Global Plugin + Global MCPとしてインストールします。

初回:

```bash
python -m pip install -e .
copilot-multi-review install --platform antigravity
```

更新:

```bash
git pull
python -m pip install -e .
copilot-multi-review sync --platform antigravity
```

状態確認:

```bash
copilot-multi-review status --platform antigravity
```

削除:

```bash
copilot-multi-review uninstall --platform antigravity
```

インストール先:

```text
~/.gemini/config/plugins/copilot-multi-review/
~/.gemini/config/mcp_config.json
```

通常利用は、レビュー対象RepositoryをAntigravityで開き、Custom Agentの `review-orchestrator` を選択して「mainとの差分をレビューして」と依頼します。Plugin同梱の `review` Skillも利用でき、Skillがslash commandとして公開される環境では `/review` から起動できます。

Antigravityでは一次Reviewerを `invoke_subagent` で独立contextのCustom Subagentとして起動し、Devil Advocate、Final Reviewer、Python MCPの `finalize_review` まで接続します。対象Repositoryへintegrationファイルやレビュー成果物は書き込みません。

CopilotとAntigravityを両方セットアップする場合:

```bash
copilot-multi-review install --platform all
copilot-multi-review sync --platform all
copilot-multi-review status --platform all
```

詳細は `docs/antigravity-integration.md` を参照してください。

## CLI

```bash
ai-review --help
ai-review validate-config
ai-review review --repo <path> --target base
ai-review review --repo <path> --target uncommitted --agent security
ai-review review --repo <path> --target staged
ai-review review --repo <path> --target commits --commits <from>..<to>
ai-review review --repo <path> --target file --file <repo-relative-path>
ai-review rerun --repo <path>
ai-review show-latest --repo <path>
ai-review cancel --repo <path>
ai-review cleanup-locks --repo <path>
ai-review cleanup-locks --repo <path> --apply
```

`python -m ai_review --help`でも起動できます。差分収集だけを確認する場合は`--no-agents`を指定します。

## VS Code

推奨拡張は`.vscode/extensions.json`に定義しています。

- Python
- Debugpy

操作:

1. このリポジトリをVS Codeで開く
2. `Ctrl+Shift+D`
3. 実行構成を選択
4. ▶を押す
5. フォルダ選択で対象Gitリポジトリを選ぶ
6. 実行確認ダイアログを確認
7. 実行する

通常のPythonファイル右上の再生ボタンではなく、実行とデバッグ画面の構成を使います。

構成:

- Copilotレビュー：全エージェント
- Copilotレビュー：未コミット差分
- Copilotレビュー：ステージ済み差分
- Copilotレビュー：Security
- Copilotレビュー：前回条件で再実行
- Copilotレビュー：前回結果を表示
- Copilotレビュー：実行停止
- Copilotレビュー：設定検証

確認ダイアログには対象リポジトリ、project ID、現在ブランチ、基準ブランチ、レビュー種別、実行エージェント、変更ファイル数、差分行数、切り捨て予定、未コミット差分、ステージ済み差分、品質チェック検出結果を表示します。

headless環境ではCLIを使います。

```bash
python -m ai_review review --repo <path> --target base
```

標準のレビュー実行モードは `subagent` です。Pythonが安全なcontextを準備し、
Copilot Review Orchestratorを1回呼び出します。従来のPython主導による
11 Agent直列実行は非推奨で、`--execution-mode legacy` の場合だけ利用できます。
`--agent` もlegacy実行専用です。

### Copilot Chat Custom Agent

VS Code Copilot Chatでは、Agent選択UIから`Review Orchestrator`を選択できます。

```text
VS Code
-> Copilot Chat
-> Agent selection
-> Review Orchestrator
```

`Review Orchestrator`はレビュー専用の入口です。標準Chat経路では最初に `copilotMultiReview/prepare_review` を呼び、Pythonが収集・検証したdiff/contextだけを専門Subagentへ渡します。一次レビュー後に`Devil Advocate`が各指摘を反証し、その後`Final Reviewer`が総合整理します。最後に `copilotMultiReview/finalize_review` がAI結果を検証し、Pythonのrule-based decisionと安全側に統合して日本語Markdownを生成します。既存Python ReviewEngine / CLIはlegacy互換経路として残します。

一次レビュー担当は互いの結果を見ずに独立レビューを行います。AIが返した `APPROVE` をそのまま最終判定にはせず、`finalize_review` の `decision` を利用者向け最終判定として扱います。

## エージェント

legacy実行では、1つのGitHub Copilot CLIを次の11種類の論理エージェントとして実行します。標準経路はCustom Agent/Subagentです。

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

## 安全制約

- Gemini API、OpenAI API、ローカルLLMは使用しません
- `shell=True`は使用しません
- 任意シェル、パイプ、リダイレクト、コマンド置換、PowerShell式評価を拒否します
- 自動修正、commit、push、mergeは行いません
- 自動fetch、checkout、resetは行いません
- 対象リポジトリへレビュー関連ファイルを書きません
- confirmedシークレットがある場合、Copilot CLIを呼ばず`BLOCKED`にします

Windowsでは`copilot.exe`、`copilot.cmd`、`copilot.bat`、`copilot`の順で解決します。`.cmd`または`.bat`だけ`COMSPEC /d /c call <resolved-path> ...`の固定引数配列で起動します。外部コマンド出力はbytesで受け取り、UTF-8、cp932、UTF-8 replacementの順でdecodeします。

## 保存場所

標準のCopilot Chat + MCP経路では、正式な詳細レビューをレビュー対象Repositoryの外へ保存します。

```text
~/.copilot/
└── copilot-multi-review/
    └── reviews/
        └── <project-id>/
            └── review.md
```

同じRepositoryを再レビューした場合は `review.md` を更新します。対象Repositoryへレビュー成果物は書き込みません。

既存legacy CLI経路の `reports/`、`runtime/`、history/latestは互換性のため残っていますが、通常のChat運用では使用しません。

## レビュー結果

正式なレビュー結果は `report.md` にMarkdownで出力します。内部のSchema・判定ロジックでは既存互換の英語enumを維持し、利用者向け表示だけを日本語へ変換します。

重要度:

| 内部値 | 利用者向け表示 |
|---|---|
| `Critical` | 致命的 |
| `Major` | 重大 |
| `Minor` | 軽微 |
| `Info` | 情報 |

観点も `correctness → 正当性`、`design_conformance → 設計整合性`、`security → セキュリティ` のように日本語表示します。

`report.md` は次の順序で構成します。

1. レビュー概要
2. 総評
3. 指摘一覧の表
4. 指摘ごとの詳細
5. 人間による確認事項
6. 反証により除外した指摘
7. Reviewer間の意見相違
8. レビュー担当の実行状況
9. レビュー実施範囲

具体的な指摘は、根拠付きで特定できる場合に `file` と `line` / `range` を付け、日本語の `message`、`rationale`、`recommendation` を表示します。場所を特定できない事項は行番号を推測せず、必要に応じてHuman Checkとして分離します。

### 結果判定

- `APPROVE`: 承認可
- `APPROVE_WITH_NOTES`: 注記あり
- `CHANGES_REQUIRED`: 修正必要
- `BLOCKED`: ブロック
- `INCONCLUSIVE`: 判定不能

機械判定値は英語のまま保持し、Markdownでは日本語ラベルと内部値を併記します。

## 検証

```bash
python -m pytest tests
python -m compileall -q ai_review
python -m ai_review --help
python -m ai_review validate-config
python -m json.tool .vscode/launch.json
python -m json.tool .vscode/settings.json
python -m json.tool .vscode/extensions.json
```

詳しい設計は`docs/architecture.md`、運用手順は`docs/operations.md`、復旧方法は`docs/troubleshooting.md`を参照してください。

## Copilot Chat recommended review

The recommended interactive review path is VS Code GitHub Copilot Chat with the `Review Orchestrator` custom agent.

1. Open the target repository in VS Code.
2. Open GitHub Copilot Chat.
3. Select `Review Orchestrator` from the agent picker.
4. Send a review request such as:

```text
Review the diff against main in this repository.
```

or:

```text
Review the changes corresponding to PR #123.
```

`Review Orchestrator` is the normal user-facing entry point. It delegates to the named specialist reviewers with Copilot's standard subagent tool calls, then invokes `Final Reviewer` as a subagent for synthesis. Use the expandable subagent tool calls in Copilot Chat to inspect the reviewer name, prompt/context, visible tool usage, and returned result. Exact icons and labels can vary by VS Code and Copilot version.

Specialist reviewers use `user-invocable: false` so they do not fill the normal agent picker, while remaining available to `Review Orchestrator` as subagents. See `docs/copilot-chat-review-ux.md` for the documented UI assumptions, Windows manual E2E steps, and the `run_id` relationship between Chat-only and Python Controller execution.

## Issue #29 Subagent Evaluation

Standard execution strategy: `native`

`native` means the standard `execution_mode = subagent` path: Python prepares safe context, invokes `Review Orchestrator` once, validates the untrusted Final Reviewer result, and applies deterministic fail-safe decision rules. It does not claim that every Copilot reviewer is guaranteed to run in parallel internally.

Legacy Python helper comparison paths remain available only for evaluation and migration:

```bash
ai-review review --repo <path> --target base --execution-mode legacy --orchestration-strategy sequential
ai-review review --repo <path> --target base --execution-mode legacy --orchestration-strategy limited_parallel --max-parallel-reviewers 2
```

Failure meanings:

- confirmed secret: `BLOCKED`, no Copilot invocation
- specialist failure: final decision must not be `APPROVE`
- Final Reviewer failure: final decision must not be `APPROVE`
- unavailable Chat UI or credit data: record `BLOCKED`, `NOT_OBSERVABLE`, or `Unavailable`, not guessed values

Evaluation records:

- `docs/subagent-evaluation.md`
- `docs/subagent-evaluation-results-2026-08-10.json`
- `tests/fixtures/subagent_evaluation_scenarios.json`

AI credits: current GitHub Copilot interfaces do not expose per-subagent/per-reviewer credit usage for this architecture. Do not estimate credits from token count, prompt length, duration, or fixed coefficients.

Issue #6 status: legacy and superseded by #23-#29 for the standard path. It was the original closed Python nine-reviewer sequential MVP and must not be restored as the standard.


## プロジェクト適合型レビュー

標準Custom Agent経路では、コードだけでなく、明示された要件・設計情報・プロジェクト固有ルールもレビュー根拠として扱えます。

追加した一次レビュー担当:

- `Design Conformance Reviewer`: 設計書と実装の整合性
- `Project Rules Reviewer`: プロジェクト固有ルールとの適合性

一次レビュー完了後、`Devil Advocate`が誤検出・過剰断定・根拠不足を反証し、その後`Final Reviewer`が総合整理します。利用者向けの成果物名・判定指標は可能な限り日本語で表記します。

詳細: `docs/project-aware-review.md`


### 実行時プロジェクト文脈

標準Review Controllerは、対象リポジトリから次を読み取り専用で収集してOrchestratorへ渡します。

- 要件文書
- Excel（.xlsx）を含む設計情報
- プロジェクト固有ルール
- 抽出時の警告・制限

Excelはopenpyxlで構造化し、ファイル名・シート名・セル座標をprovenanceとして保持します。.xls / PDFは自動で内容を補完せず、未対応形式として警告します。
