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
## リポジトリ全体監査で上限超過になる

`ai-review audit` は開始前に対象ファイル数、推定総行数、バッチ数、想定Copilot呼び出し数を計算します。上限を超える場合は自動的に無視せず、`--profile quick` を使う、`--max-*` を明示する、または差分レビューへ戻してください。日常運用では `base`、`uncommitted`、`staged` の差分レビューを推奨します。

## 実行時間が長い

進捗は次で確認できます。

```bash
ai-review status --repo <path>
ai-review status --repo <path> --watch
```

表示される現在バッチ、現在エージェント、完了バッチ、失敗バッチ、BLOCKEDバッチ、待機バッチを見てください。停止したい場合は次を使います。

```bash
ai-review cancel --repo <path>
```

cancelは現在のrun IDだけを対象にし、広範囲なプロセスkillは行いません。

## coverage.jsonの見方

`coverage.json` はファイル単位の監査状態です。

- `reviewed`: バッチ内でレビュー済み
- `excluded`: 既定除外、バイナリ、大容量、secret fileなどで対象外
- `skipped`: 安全にレビューできずskip
- `failed`: バッチまたはエージェント失敗
- `blocked`: confirmed secretなどで送信停止
- `unreviewed`: 未確認のまま終了

`unreviewed`、`failed`、`blocked` が残る場合、最終判定は無条件に `APPROVE` になりません。

巨大ファイルはsegmentへ分割されます。`segments` 配列の `start_line` / `end_line` を確認してください。1行だけで文字数上限を超える場合は `skipped` になり、理由は `single_line_exceeds_char_limit` です。

## シークレットでBLOCKEDになる

confirmed secretが1件でもある場合、リポジトリ全体監査は `BLOCKED` になり、Copilot呼び出し回数は0になります。`.env`、PEM、秘密鍵、token、DB接続文字列などはCopilotへ送信しません。検出器定義、テストfixture、ドキュメントサンプルは文脈に応じて非ブロッキング扱いしますが、実Tokenや実PEMはブロックします。

`.env`、`.env.*`、`*.key`、`*.pem`、`*.p12` がGit管理対象または `--include-untracked` で明示対象に入る場合は、既定で `confirmed_secret_file` として全体を `BLOCKED` にします。MVPでは `tests`、`sample`、`example`、`docs` などのパスsubstringだけで非ブロッキング化しません。

## analysis-onlyとINCONCLUSIVEの違い

`--no-agents` は `analysis_only` であり、レビュー失敗ではありません。Copilot呼び出しは0で、batch計画とcoverage初期状態だけを保存します。通常監査で失敗、cancel、未確認が残った場合は `INCONCLUSIVE` です。

## failedとcancelledの違い

Copilot例外やschema不一致などは `failed` です。ユーザーのcancel要求は `cancelled` で、後続batchは `skipped` になります。cancelledはfailedに混ぜません。

## Copilot例外分類

レポートには `authentication`、`rate_limit`、`timeout`、`schema_validation`、`cancelled`、`process_start`、`network`、`unexpected` のいずれかを保存します。完全prompt、秘密値、未マスクstdout/stderr、stack trace全文は保存しません。

## status --watchが更新されない

`status --watch` は状態変化時だけ再出力します。同じ表示が続く場合は、重複出力を抑制している状態です。別ターミナルで `ai-review status --repo <path>` を一度だけ実行すると現在値を確認できます。

## 横断統合でBLOCKEDになる

batch resultやcross resultにtoken風文字列が含まれると、次のagentへ送る前の実payload検査で `BLOCKED` になります。これは前段agentの出力を再送信しないための保護です。レポートには `blocked_phase`、`blocked_agent`、`blocked_source` のような安全なmetadataだけを保存します。

## レビュー可能ファイルがない

空リポジトリ、binaryのみ、large fileのみ、既定除外のみの場合、通常監査はCopilotを呼ばず `INCONCLUSIVE` になります。`repository-summary.json` の `no_reviewable_files` と `reviewable_segment_count` を確認してください。

## エラー詳細が見えない

Copilot実行エラーは秘密値流出を避けるため定型文で保存します。stdout、stderr、prompt断片、stack trace全文はreportへ保存しません。

## Windowsで文字化けやBAT/CMD起動問題がある

外部コマンド出力はbytesで受け、UTF-8 strict、cp932 strict、UTF-8 replaceの順でdecodeします。Copilot CLIが `.bat` または `.cmd` の場合は `COMSPEC /d /c call <resolved-path> <args...>` で固定引数起動します。`COMSPEC` が壊れている場合は環境変数を確認してください。
