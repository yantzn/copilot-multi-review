from __future__ import annotations

from ai_review import mcp_server


def test_mcp_server_exposes_prepare_and_finalize_tools() -> None:
    assert mcp_server.mcp.name == "copilotMultiReview"
    assert callable(mcp_server.prepare_review)
    assert callable(mcp_server.finalize_review)
