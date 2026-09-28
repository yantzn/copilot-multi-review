---
name: review
description: 複数の専門Reviewer、反証、Final Reviewer、Python MCP安全判定を使って現在のRepositoryをコードレビューする。コードレビュー、差分レビュー、PRレビュー、mainとの差分レビューを依頼されたときに使用する。
---

# Project-aware multi review

このSkillは、現在のworkspaceを `copilot-multi-review` のレビュー基盤でレビューするための入口です。

## 実行方針

1. `review-orchestrator` Custom Agentが利用可能なら、そのAgentへユーザーのレビュー依頼をそのまま委譲してください。
2. `review-orchestrator` が利用できない場合、通常レビューへフォールバックせず、利用者へAntigravity Pluginのinstall/status確認を案内してください。
3. レビューはread-onlyです。ファイルを修正、commit、push、mergeしないでください。
4. 正式な最終判定はAI単独ではなく、`copilotMultiReview` MCPの `finalize_review` が返すdecisionを使用してください。
5. 詳細な正式成果物は `finalize_review` が生成する日本語Markdownです。

## 通常の依頼例

- mainとの差分をレビューして
- 未コミット差分をレビューして
- staged差分をレビューして
- この変更を要件・設計書・プロジェクトルールも含めてレビューして
