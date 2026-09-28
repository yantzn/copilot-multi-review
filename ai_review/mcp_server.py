from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .mcp_review import finalize_review_context, prepare_review_context


mcp = MCPServer(
    "copilotMultiReview",
    instructions=(
        "コードレビューの決定論的な前処理と最終安全判定を提供します。"
        "prepare_reviewを最初に呼び、返されたreview_context_idを"
        "Final Reviewer完了後のfinalize_reviewへ渡してください。"
    ),
)


@mcp.tool()
def prepare_review(
    repository_path: str = ".",
    target: str = "base",
    base_branch: str | None = None,
    commits: str | None = None,
    file_path: str | None = None,
    run_quality: bool = True,
) -> dict[str, object]:
    """レビュー対象の安全なdiff/contextを準備する。

    confirmed secretを検出した場合はdiffを返さずblockedにする。
    targetは base / staged / uncommitted / commits / file を指定する。
    """

    return prepare_review_context(
        repository_path,
        target=target,
        base_branch=base_branch,
        commits=commits,
        file_path=file_path,
        run_quality=run_quality,
    )


@mcp.tool()
def finalize_review(
    review_context_id: str,
    final_result: dict[str, Any],
) -> dict[str, object]:
    """Final Reviewer結果を検証し、安全側判定と日本語Markdownを生成する。"""

    return finalize_review_context(
        review_context_id,
        dict(final_result),
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
