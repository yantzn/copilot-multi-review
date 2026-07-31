# copilot-multi-review

GitHub Copilot CLI専用のローカル・マルチエージェントコードレビュー基盤です。

このリポジトリにレビューエンジン、共通設定、プロンプト、Schema、レポート、runtimeを集約し、レビュー対象の外部Gitリポジトリにはレビュー用ファイルを作成しません。

## 初期セットアップ

Python 3.11以上を使用してください。

```bash
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e .[dev]
```

macOS/Linuxでは次のように仮想環境を有効化します。

```bash
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .[dev]
```

GitHub Copilot CLIを導入し、次のコマンドが成功する状態にしてください。

```bash
copilot version
```

認証が必要な場合は、Copilot CLIの案内に従ってログインしてください。

## CLI

```bash
ai-review --help
ai-review validate-config
ai-review review --repo <path> --target base
ai-review review --repo <path> --target base --base-branch main
ai-review show-latest --repo <path>
ai-review cancel --repo <path>
```

`python -m ai_review --help`でも起動できます。

`review --repo <path>`は外部のローカルGitリポジトリを検証し、worktree、remote、現在ブランチ、HEAD SHA、基準ブランチ、project IDを表示します。
あわせて`base`、`uncommitted`、`staged`、`commits`、`file`の差分規模、変更ファイル、README等の要件候補、品質チェック結果をCopilot実行前に収集します。

基準ブランチは次の順で判定します。

1. `--base-branch`
2. `origin/HEAD`
3. `main`
4. `develop`

判定できない場合は、自動fetchせずにエラーになります。

品質チェックは共通allowlistに含まれるコマンドのみ実行できます。パイプ、リダイレクト、コマンド置換、PowerShell式評価、`git fetch`、`git checkout`、`git reset`は拒否します。

エージェント実行では、1つのGitHub Copilot CLIを9種類の論理エージェントとして次の順に直列実行します。

1. requirements
2. correctness
3. security
4. testing
5. maintainability
6. performance
7. operations
8. devil_advocate
9. final

単独エージェントは`--agent security`のように指定できます。差分収集だけを確認したい場合は`--no-agents`を指定してください。

Copilot送信前には差分内のシークレット候補を検査します。confirmed候補が1件でもある場合、Copilot CLIを呼ばずに`BLOCKED`として扱います。検出器定義や専用テストfixtureの自己誤検知は、ファイル位置と文脈を確認して非ブロッキングに分類します。

Windowsでは`copilot.exe`、`copilot.cmd`、`copilot.bat`、`copilot`の順でGitHub Copilot CLIを解決します。`.cmd`または`.bat`の場合だけ、`COMSPEC /d /c call <resolved-path> ...`の固定引数配列で起動します。外部コマンド出力はbytesで受け取り、UTF-8、cp932、UTF-8 replacementの順でdecodeします。

現時点では`show-latest`、`cancel`は入口だけを定義しており、後続Issueで実装します。未実装コマンドは明確なエラーを返します。

## ディレクトリ

```text
ai_review/  CLIと実行基盤
agents/     論理エージェント別プロンプト
schemas/    JSON Schema
config/     共通設定
reports/    レビュー結果。Git管理対象外
runtime/    実行状態とロック。Git管理対象外
```

## VS Code

推奨拡張は`.vscode/extensions.json`に定義しています。

- Python
- Debugpy

後続Issueで、VS Codeの実行とデバッグ画面から対象リポジトリ選択と実行確認を行う構成を追加します。

## 検証

```bash
python -m pytest tests
python -m py_compile ai_review/*.py
python -m ai_review --help
python -m ai_review validate-config
```
