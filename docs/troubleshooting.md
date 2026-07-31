# Troubleshooting

## Python 3.10が選ばれる

VS Codeの`Python: Select Interpreter`でPython 3.11以上の`.venv`を選んでください。

## Copilot CLIが見つからない

```bash
copilot version
ai-review validate-config
```

どちらも失敗する場合はGitHub Copilot CLIを導入し、認証してください。

## BAT/CMDでWinError 2

Windowsでは`.cmd`または`.bat`を`COMSPEC /d /c call`経由で起動します。`COMSPEC`が壊れている場合はPowerShellで確認してください。

```powershell
$env:COMSPEC
```

## cp932 UnicodeDecodeError

外部コマンド出力はbytesで受け、UTF-8、cp932、UTF-8 replacementの順でdecodeします。再発する場合は該当コマンド名とstdout/stderrの文字コードを確認してください。

## シークレット検出

confirmed候補があるとCopilot CLIを呼ばず`BLOCKED`になります。秘密値はレポートへ保存されません。実値を削除してから再実行してください。

## 自己誤検知

検出器定義や専用fixture内のサンプルは`detector_definition`または`test_fixture`として非ブロッキングに分類します。実PEMや実Tokenは引き続きブロックします。

## テスト失敗

```bash
python -m pytest tests
```

品質チェック失敗は`failed`として扱い、最終判定は安全側へ倒します。

## 巨大差分

変更ファイル数、差分行数、切り捨て有無を記録します。切り捨てがある場合は無条件`APPROVE`にしません。

## 実行ロック

同一project IDの二重起動は拒否します。通常は完了時に自分のlock/runningだけ解放します。

## stale lock

まずdry-runします。

```bash
ai-review cleanup-locks --repo <path>
```

内容を確認してから明示削除します。

```bash
ai-review cleanup-locks --repo <path> --apply
```

## 保存失敗

`reports/`と`runtime/`を書き込めるか確認してください。対象リポジトリではなく、この専用リポジトリ側の権限を確認します。
