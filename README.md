# copilot-multi-review

GitHub Copilot CLI専用のローカル・マルチエージェントコードレビュー基盤です。

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

GitHub Copilot CLIを導入し、認証してください。

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

### Copilot Chat Custom Agent

VS Code Copilot Chatでは、Agent選択UIから`Review Orchestrator`を選択できます。

```text
VS Code
-> Copilot Chat
-> Agent selection
-> Review Orchestrator
```

`Review Orchestrator`はレビュー専用の入口です。詳細レビューは専門Subagentへ委譲し、Python ReviewEngine / CLIレビュー経路は維持します。詳しい責務境界は`docs/architecture.md`を参照してください。

専門Reviewerは互いの結果を見ずに独立レビューを行い、`Final Reviewer`だけが全専門結果を受け取って重複排除、provenance保持、矛盾整理、AI統合decision候補生成を担当します。最終判定では既存のPython rule-based decisionとの安全側統合を維持します。

## エージェント

1つのGitHub Copilot CLIを、次の9種類の論理エージェントとして完全に直列実行します。最大同時Copilot呼び出し数は1です。

1. requirements
2. correctness
3. security
4. testing
5. maintainability
6. performance
7. operations
8. devil_advocate
9. final

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

```text
reports/
└── <project-id>/
    ├── latest/
    │   ├── run.json
    │   ├── final.json
    │   ├── report.md
    │   └── agents/
    └── history/
        └── <run-id>/

runtime/
└── <project-id>/
    ├── review.lock
    ├── mutation.lock
    ├── cleanup.lock
    ├── running.json
    └── cancel.json
```

`reports/`と`runtime/`はGit管理対象外です。

## 結果判定

- `APPROVE`: 指摘なし
- `APPROVE_WITH_NOTES`: MinorまたはInfoのみ
- `CHANGES_REQUIRED`: Majorあり
- `BLOCKED`: Criticalまたはブロッキングシークレットあり
- `INCONCLUSIVE`: 情報不足、切り捨て、失敗、キャンセル、品質チェック失敗など

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
