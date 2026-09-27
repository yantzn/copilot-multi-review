# プロジェクト適合型コードレビュー

## 目的

コードだけでなく、要件・設計書・プロジェクト固有ルール・テスト結果を根拠としてレビューし、一次レビュー、反証レビュー、総合整理、人間判断を分離する。

## 標準フロー

```text
PR / diff
  ↓
要件・設計情報・プロジェクト固有ルールの準備
  ↓
Review Orchestrator
  ↓
一次レビュー担当（独立）
  ├─ Requirements Reviewer
  ├─ Correctness Reviewer
  ├─ Design Conformance Reviewer
  ├─ Project Rules Reviewer
  ├─ Security Reviewer
  ├─ Testing Reviewer
  ├─ Maintainability Reviewer
  ├─ Performance Reviewer
  └─ Operations Reviewer
  ↓
Devil Advocate（反証レビュー）
  ↓
Final Reviewer（総合整理）
  ↓
Python deterministic decision
  ↓
人間の最終判断
```

## Excelなどの設計書

Excelを「添付したから読めている」とは扱わない。設計書は可能な限り読み取り専用の解析処理で構造化し、次の参照元情報を保持した Design Context としてレビューへ渡す。

- ファイル名
- シート名
- セル範囲
- 設計項目ID
- 仕様値
- 抽出時の注意事項

複雑な結合セル、図形、色、コメント、非表示行列などが意味を持つ場合は、抽出欠落の可能性を `missing_context` として扱う。

## 設計整合性

Design Conformance Reviewer は、コードと明示された設計情報を比較する。設計書にない仕様を補完しない。

要件と設計書が矛盾した場合は、「実装誤り」と断定せず人間確認へ送る。

## プロジェクトルール適合性

Project Rules Reviewer は明示されたルールだけを根拠にする。

機械判定できるルールは lint / 静的解析 / CI の結果を優先し、意味理解が必要な規約をAIレビューへ残す。ルール例外の可否は人間判断とする。

## 反証レビュー

Devil Advocate は一次レビュー後に実行し、各指摘を次のいずれかに分類する。

- 維持候補
- 重大度見直し候補
- 人間確認へ移動
- 根拠不足による棄却候補
- 追加情報が必要

「反対すること」自体を目的にしない。別解が存在するだけでは正しい指摘を棄却しない。

## 総合整理

Final Reviewer は一次レビューと反証結果を統合する。

- 重複指摘を統合
- provenance を保持
- 反証後も成立する指摘を残す
- 根拠不足の指摘を除外・保留
- 要件・設計・ルール間の矛盾を人間確認へ移動
- レビュー担当間の意見相違を残す
- 未確認範囲を明示

Final Reviewer の decision はAI側の候補であり、最終権限ではない。

## ベンチマークで確認する判定指標

- 正検出数
- 見逃し数
- 誤検出数
- 根拠整合率
- 設計書参照精度
- プロジェクトルール判定精度
- 人間確認振り分け精度
- 重複指摘率
- 反証による誤検出除外数
- 正しい指摘の誤棄却数
- 反証後の誤検出率
- 意見相違検出数
- レビュー実施範囲

件数と率の両方を残し、「指摘数が多いほど良い」という評価にはしない。
