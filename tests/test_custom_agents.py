from __future__ import annotations

from pathlib import Path

import pytest

from ai_review.custom_agents import (
    CustomAgentValidationError,
    validate_custom_agent_schema,
    validate_custom_agents,
)


def write_agent(root: Path, content: str, name: str = "review-orchestrator.agent.md") -> Path:
    agents_dir = root / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    path = agents_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def test_review_orchestrator_agent_definition_exists_and_is_valid() -> None:
    definitions = validate_custom_agents(Path.cwd())
    orchestrator = next(item for item in definitions if item.name == "Review Orchestrator")

    assert orchestrator.path.name == "review-orchestrator.agent.md"
    assert orchestrator.description
    assert "agent" in orchestrator.tools
    assert orchestrator.agents == "*"
    assert "Review Orchestrator" in orchestrator.instructions
    assert "Final Reviewer" in orchestrator.instructions
    assert "Subagent Result Contract" in orchestrator.instructions
    assert "git push" in orchestrator.instructions
    assert orchestrator.metadata["argument-hint"]


def test_list_tools_are_valid(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
tools:
  - search/codebase
  - agent
agents: '*'
---
Instructions
""",
    )

    definitions = validate_custom_agents(tmp_path)

    assert definitions[0].tools == ["search/codebase", "agent"]


def test_string_tools_are_normalized(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Search Reviewer
description: Coordinate review.
tools: search
---
Instructions
""",
    )

    definitions = validate_custom_agents(tmp_path)

    assert definitions[0].tools == ["search"]


def test_missing_tools_are_valid_custom_agent_schema(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Generic Agent
description: Generic custom agent.
---
Instructions
""",
    )

    definitions = validate_custom_agent_schema(tmp_path)

    assert definitions[0].tools is None


def test_missing_tools_are_rejected_by_review_only_policy(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
---
Instructions
""",
    )

    with pytest.raises(CustomAgentValidationError, match="review-only policy requires explicit tools"):
        validate_custom_agents(tmp_path)


def test_nested_yaml_is_valid(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
tools:
  - agent
agents: '*'
handoffs:
  - label: Final review
    agent: final-reviewer
    prompt: Integrate reviewer results.
---
Instructions
""",
    )

    definitions = validate_custom_agents(tmp_path)

    assert definitions[0].metadata["handoffs"] == [
        {
            "label": "Final review",
            "agent": "final-reviewer",
            "prompt": "Integrate reviewer results.",
        }
    ]


def test_unknown_frontmatter_fields_are_allowed(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
tools: search
future-field:
  nested:
    value: true
---
Instructions
""",
    )

    definitions = validate_custom_agents(tmp_path)

    assert definitions[0].metadata["future-field"] == {"nested": {"value": True}}


def test_architecture_documents_python_and_custom_agent_boundaries() -> None:
    text = Path("docs/architecture.md").read_text(encoding="utf-8")

    assert "Python ReviewEngine Responsibilities" in text
    assert "Copilot Custom Agent Responsibilities" in text
    assert "Review Orchestrator" in text
    assert "VS Code Copilot Chat" in text
    assert "Python ReviewEngine" in text
    assert "Copilot CLI" in text


def test_invalid_frontmatter_is_rejected(tmp_path: Path) -> None:
    write_agent(tmp_path, "name: Review Orchestrator\n\ninstructions")

    with pytest.raises(CustomAgentValidationError, match="frontmatter"):
        validate_custom_agents(tmp_path)


def test_missing_required_metadata_is_rejected(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
tools: ['agent']
agents: '*'
---
Instructions
""",
    )

    with pytest.raises(CustomAgentValidationError, match="description"):
        validate_custom_agents(tmp_path)


def test_empty_agent_instructions_are_rejected(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
tools: ['agent']
agents: '*'
---
""",
    )

    with pytest.raises(CustomAgentValidationError, match="instructions"):
        validate_custom_agents(tmp_path)


def test_invalid_agent_definition_is_rejected(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
tools: ['search/codebase']
agents: '*'
---
Instructions
""",
    )

    with pytest.raises(CustomAgentValidationError, match="review-only policy requires the agent tool"):
        validate_custom_agents(tmp_path)


def test_duplicate_agent_names_are_rejected(tmp_path: Path) -> None:
    content = """---
name: Review Orchestrator
description: Coordinate review.
tools: []
---
Instructions
"""
    write_agent(tmp_path, content, "one.agent.md")
    (tmp_path / ".github" / "agents" / "two.agent.md").write_text(content, encoding="utf-8")

    with pytest.raises(CustomAgentValidationError, match="duplicate"):
        validate_custom_agents(tmp_path)


def test_banned_review_tools_are_rejected(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
tools: ['edit']
---
Instructions
""",
    )

    with pytest.raises(CustomAgentValidationError, match="review-only policy forbids"):
        validate_custom_agents(tmp_path)


def test_banned_tool_family_is_rejected(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
tools: ['terminal/runCommand']
---
Instructions
""",
    )

    with pytest.raises(CustomAgentValidationError, match="review-only policy forbids"):
        validate_custom_agents(tmp_path)


def test_invalid_tools_type_is_rejected(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
tools:
  search: true
---
Instructions
""",
    )

    with pytest.raises(CustomAgentValidationError, match="tools must be"):
        validate_custom_agents(tmp_path)


def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    write_agent(
        tmp_path,
        """---
name: Review Orchestrator
description: Coordinate review.
tools:
  - agent
  - [broken
---
Instructions
""",
    )

    with pytest.raises(CustomAgentValidationError, match="YAML frontmatter is invalid"):
        validate_custom_agents(tmp_path)
