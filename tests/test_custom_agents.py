from __future__ import annotations

from pathlib import Path

import pytest

from ai_review.custom_agents import (
    CustomAgentValidationError,
    FINAL_REVIEWER,
    SPECIALIST_REVIEWERS,
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
    assert set(orchestrator.agents) >= set(SPECIALIST_REVIEWERS.values())
    assert set(orchestrator.agents) >= set(FINAL_REVIEWER.values())
    assert "Review Orchestrator" in orchestrator.instructions
    assert "Final Reviewer" in orchestrator.instructions
    assert "Subagent Result Contract" in orchestrator.instructions
    assert "git push" in orchestrator.instructions
    assert orchestrator.metadata["argument-hint"]


def test_all_specialist_reviewer_agent_files_exist() -> None:
    agents_dir = Path(".github/agents")

    for file_name in SPECIALIST_REVIEWERS:
        assert (agents_dir / file_name).is_file()


def test_final_reviewer_agent_definition_exists_and_is_valid() -> None:
    definitions = validate_custom_agents(Path.cwd())
    final = next(item for item in definitions if item.name == "Final Reviewer")

    assert final.path.name == "final-reviewer.agent.md"
    assert final.metadata.get("user-invocable") is False
    assert final.tools
    assert "agent" not in final.tools
    assert final.agents is None


def test_expected_specialist_reviewers_are_loaded() -> None:
    definitions = validate_custom_agents(Path.cwd())
    names = {definition.name for definition in definitions}

    assert set(SPECIALIST_REVIEWERS.values()) <= names
    assert set(FINAL_REVIEWER.values()) <= names


def test_specialist_reviewers_are_hidden_from_normal_agent_picker() -> None:
    definitions = validate_custom_agents(Path.cwd())
    by_file = {definition.path.name: definition for definition in definitions}

    for file_name in SPECIALIST_REVIEWERS:
        assert by_file[file_name].metadata.get("user-invocable") is False


def test_specialist_reviewers_are_read_only_leaf_agents() -> None:
    definitions = validate_custom_agents(Path.cwd())
    by_file = {definition.path.name: definition for definition in definitions}

    for file_name in SPECIALIST_REVIEWERS:
        definition = by_file[file_name]
        assert definition.tools
        assert "agent" not in definition.tools
        assert definition.agents is None
        assert {"edit", "terminal", "runCommands", "runTask", "notebookEdit"}.isdisjoint(definition.tools)


def test_final_reviewer_is_read_only_leaf_agent() -> None:
    definitions = validate_custom_agents(Path.cwd())
    final = next(item for item in definitions if item.name == "Final Reviewer")

    assert final.tools
    assert "agent" not in final.tools
    assert final.agents is None
    assert {"edit", "terminal", "runCommands", "runTask", "notebookEdit"}.isdisjoint(final.tools)
    for forbidden in [
        "git commit",
        "git push",
        "git merge",
        "git reset",
        "git checkout",
        "git clean",
        "git rebase",
        "git tag",
    ]:
        assert forbidden in final.instructions


def test_orchestrator_delegates_to_all_specialist_reviewers() -> None:
    definitions = validate_custom_agents(Path.cwd())
    orchestrator = next(item for item in definitions if item.name == "Review Orchestrator")

    assert isinstance(orchestrator.agents, list)
    assert set(SPECIALIST_REVIEWERS.values()) <= set(orchestrator.agents)
    assert set(FINAL_REVIEWER.values()) <= set(orchestrator.agents)
    for reviewer_name in SPECIALIST_REVIEWERS.values():
        assert reviewer_name in orchestrator.instructions
    assert "Final Reviewer Input Contract" in orchestrator.instructions
    assert "Keep specialist reviewers independent" in orchestrator.instructions


def test_specialist_reviewers_include_common_finding_contract() -> None:
    definitions = validate_custom_agents(Path.cwd())
    by_file = {definition.path.name: definition for definition in definitions}
    required_terms = [
        "severity",
        "category",
        "file",
        "line/range",
        "message",
        "rationale",
        "recommendation",
        "confidence",
        "Critical",
        "Major",
        "Minor",
        "Info",
    ]

    for file_name in SPECIALIST_REVIEWERS:
        instructions = by_file[file_name].instructions
        for term in required_terms:
            assert term in instructions


def test_specialist_reviewers_document_independent_evaluation() -> None:
    definitions = validate_custom_agents(Path.cwd())
    by_file = {definition.path.name: definition for definition in definitions}

    for file_name in SPECIALIST_REVIEWERS:
        instructions = by_file[file_name].instructions.lower()
        assert "do not use other reviewer results before review" in instructions
        assert "previous reviewer conclusions" in instructions
        assert "independently evaluate the same diff/context" in instructions


def test_final_reviewer_documents_input_contract() -> None:
    definitions = validate_custom_agents(Path.cwd())
    final = next(item for item in definitions if item.name == "Final Reviewer")

    for term in [
        "review_target",
        "repository",
        "base_ref",
        "head_ref",
        "changed_files",
        "truncation_status",
        "secret_scan_status",
        "quality_check_status",
        "specialist_results",
        "reviewer_states",
    ]:
        assert term in final.instructions


def test_final_reviewer_documents_dedup_and_provenance_contract() -> None:
    definitions = validate_custom_agents(Path.cwd())
    final = next(item for item in definitions if item.name == "Final Reviewer")
    instructions = final.instructions.lower()

    assert "merge duplicate findings" in instructions
    assert "semantic similarity" in instructions
    assert "reported_by" in final.instructions
    assert "reported_severities" in final.instructions
    assert "Critical and Major" in final.instructions


def test_final_reviewer_documents_severity_conflict_resolution() -> None:
    definitions = validate_custom_agents(Path.cwd())
    final = next(item for item in definitions if item.name == "Final Reviewer")

    assert "Critical > Major > Minor > Info" in final.instructions
    assert "severity_conflict: true" in final.instructions
    assert "choose the highest severity" in final.instructions


def test_final_reviewer_documents_conflicts_and_incomplete_review() -> None:
    definitions = validate_custom_agents(Path.cwd())
    final = next(item for item in definitions if item.name == "Final Reviewer")
    instructions = final.instructions.lower()

    assert "do not hide clear contradictions" in instructions
    assert "conflicts" in instructions
    assert "failed" in instructions
    assert "missing" in instructions
    assert "not_run" in instructions
    assert "incomplete_review" in instructions
    assert "do not propose unconditional `approve`" in instructions
    assert "truncated" in instructions
    assert "INCONCLUSIVE" in final.instructions


def test_final_reviewer_uses_existing_decision_vocabulary() -> None:
    definitions = validate_custom_agents(Path.cwd())
    final = next(item for item in definitions if item.name == "Final Reviewer")

    for decision in ["APPROVE", "APPROVE_WITH_NOTES", "CHANGES_REQUIRED", "BLOCKED", "INCONCLUSIVE"]:
        assert decision in final.instructions
    assert "AI synthesis decision candidate" in final.instructions
    assert "stricter_decision" in final.instructions


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

    definitions = validate_custom_agents(tmp_path, require_specialist_reviewers=False)

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

    definitions = validate_custom_agents(tmp_path, require_specialist_reviewers=False)

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
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)


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

    definitions = validate_custom_agents(tmp_path, require_specialist_reviewers=False)

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

    definitions = validate_custom_agents(tmp_path, require_specialist_reviewers=False)

    assert definitions[0].metadata["future-field"] == {"nested": {"value": True}}


def test_architecture_documents_python_and_custom_agent_boundaries() -> None:
    text = Path("docs/architecture.md").read_text(encoding="utf-8")

    assert "Python Review Controller Responsibilities" in text
    assert "Copilot Custom Agent Responsibilities" in text
    assert "Review Orchestrator" in text
    assert "VS Code Copilot Chat" in text
    assert "Python Review Controller" in text
    assert "AI output is untrusted input" in text
    assert "Copilot CLI" in text
    for reviewer in [
        "requirements",
        "correctness",
        "security",
        "testing",
        "maintainability",
        "performance",
        "operations",
        "devil_advocate",
    ]:
        assert reviewer in text


def test_invalid_frontmatter_is_rejected(tmp_path: Path) -> None:
    write_agent(tmp_path, "name: Review Orchestrator\n\ninstructions")

    with pytest.raises(CustomAgentValidationError, match="frontmatter"):
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)


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
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)


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
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)


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
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)


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
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)


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
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)


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
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)


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
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)


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
        validate_custom_agents(tmp_path, require_specialist_reviewers=False)
