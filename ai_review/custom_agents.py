from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class CustomAgentValidationError(RuntimeError):
    """Raised when a VS Code Copilot custom agent definition is invalid."""


@dataclass(frozen=True)
class CustomAgentDefinition:
    path: Path
    name: str
    description: str
    tools: list[str] | None
    agents: list[str] | str | None
    instructions: str
    metadata: dict[str, object]


BANNED_REVIEW_TOOLS = {
    "edit",
    "terminal",
    "runCommands",
    "runTask",
    "notebookEdit",
}

SPECIALIST_REVIEWERS = {
    "requirements-reviewer.agent.md": "Requirements Reviewer",
    "correctness-reviewer.agent.md": "Correctness Reviewer",
    "security-reviewer.agent.md": "Security Reviewer",
    "testing-reviewer.agent.md": "Testing Reviewer",
    "maintainability-reviewer.agent.md": "Maintainability Reviewer",
    "performance-reviewer.agent.md": "Performance Reviewer",
    "operations-reviewer.agent.md": "Operations Reviewer",
    "devil-advocate.agent.md": "Devil Advocate",
}

ORCHESTRATOR_NAME = "Review Orchestrator"


def validate_custom_agents(
    root: Path | None = None,
    *,
    enforce_review_policy: bool = True,
    require_specialist_reviewers: bool = True,
) -> list[CustomAgentDefinition]:
    definitions = validate_custom_agent_schema(root)
    if enforce_review_policy:
        validate_review_agent_policy(definitions, require_specialist_reviewers=require_specialist_reviewers)
    return definitions


def validate_custom_agent_schema(root: Path | None = None) -> list[CustomAgentDefinition]:
    root = root or Path.cwd()
    agents_dir = root / ".github" / "agents"
    if not agents_dir.is_dir():
        raise CustomAgentValidationError(".github/agents directory is missing")

    definitions = [_load_agent(path) for path in sorted(agents_dir.glob("*.agent.md"))]
    if not definitions:
        raise CustomAgentValidationError(".github/agents must contain at least one *.agent.md file")

    names: set[str] = set()
    duplicates: set[str] = set()
    for definition in definitions:
        if definition.name in names:
            duplicates.add(definition.name)
        names.add(definition.name)
    if duplicates:
        raise CustomAgentValidationError(f"duplicate custom agent name: {', '.join(sorted(duplicates))}")

    return definitions


def validate_review_agent_policy(
    definitions: list[CustomAgentDefinition],
    *,
    require_specialist_reviewers: bool = True,
) -> None:
    by_file = {definition.path.name: definition for definition in definitions}
    by_name = {definition.name: definition for definition in definitions}

    orchestrator = by_name.get(ORCHESTRATOR_NAME)
    if require_specialist_reviewers:
        missing_files = sorted(set(SPECIALIST_REVIEWERS) - set(by_file))
        if missing_files:
            raise CustomAgentValidationError(
                f"review-only policy requires specialist reviewer agents: {', '.join(missing_files)}"
            )
        if orchestrator is None:
            raise CustomAgentValidationError("review-only policy requires Review Orchestrator")

    for definition in definitions:
        tools = definition.tools
        if tools is None:
            raise CustomAgentValidationError(
                f"{definition.path}: review-only policy requires explicit tools to limit agent capabilities"
            )
        if any(_is_banned_tool(tool) for tool in tools):
            raise CustomAgentValidationError(
                f"{definition.path}: review-only policy forbids editing or terminal tools"
            )
        if definition.agents is not None and "agent" not in tools:
            raise CustomAgentValidationError(
                f"{definition.path}: review-only policy requires the agent tool when agents are configured"
            )

    expected_names = set(SPECIALIST_REVIEWERS.values())
    if orchestrator is not None and orchestrator.agents != "*" and not (
        isinstance(orchestrator.agents, list) and expected_names <= set(orchestrator.agents)
    ):
        if require_specialist_reviewers or expected_names & set(orchestrator.agents or []):
            raise CustomAgentValidationError(
                f"{orchestrator.path}: review-only policy requires orchestrator access to all specialist reviewers"
            )

    for file_name, expected_name in SPECIALIST_REVIEWERS.items():
        definition = by_file.get(file_name)
        if definition is None:
            continue
        if definition.name != expected_name:
            raise CustomAgentValidationError(
                f"{definition.path}: review-only policy expects agent name {expected_name!r}"
            )
        if definition.metadata.get("user-invocable") is not False:
            raise CustomAgentValidationError(
                f"{definition.path}: review-only policy requires user-invocable: false"
            )
        if definition.agents is not None:
            raise CustomAgentValidationError(
                f"{definition.path}: specialist reviewers must not configure subagents"
            )
        if definition.tools and "agent" in definition.tools:
            raise CustomAgentValidationError(
                f"{definition.path}: specialist reviewers must not enable the agent tool"
            )


def _load_agent(path: Path) -> CustomAgentDefinition:
    text = path.read_text(encoding="utf-8")
    metadata, instructions = _split_frontmatter(text, path)

    name = metadata.get("name") or path.name.removesuffix(".agent.md")
    description = metadata.get("description")
    tools = _normalize_tools(metadata.get("tools"), path)
    agents = metadata.get("agents")

    if not isinstance(name, str) or not name.strip():
        raise CustomAgentValidationError(f"{path}: name must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise CustomAgentValidationError(f"{path}: description must be a non-empty string")
    if agents is not None and not _valid_agents_value(agents):
        raise CustomAgentValidationError(f"{path}: agents must be '*', [], or a list of non-empty strings")
    if not instructions.strip():
        raise CustomAgentValidationError(f"{path}: agent instructions must not be empty")

    return CustomAgentDefinition(
        path=path,
        name=name.strip(),
        description=description.strip(),
        tools=tools,
        agents=agents,
        instructions=instructions.strip(),
        metadata=metadata,
    )


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise CustomAgentValidationError(f"{path}: YAML frontmatter is required")

    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise CustomAgentValidationError(f"{path}: YAML frontmatter is not closed") from exc

    frontmatter = "\n".join(lines[1:end])
    try:
        parsed = yaml.safe_load(frontmatter) if frontmatter.strip() else {}
    except yaml.YAMLError as exc:
        raise CustomAgentValidationError(f"{path}: YAML frontmatter is invalid: {exc}") from exc
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise CustomAgentValidationError(f"{path}: YAML frontmatter must be a mapping")

    instructions = "\n".join(lines[end + 1 :])
    return parsed, instructions


def _normalize_tools(value: object, path: Path) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            raise CustomAgentValidationError(f"{path}: tools must not contain empty strings")
        return [value.strip()]
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return [item.strip() for item in value]
    raise CustomAgentValidationError(f"{path}: tools must be a string or a list of non-empty strings")


def _is_banned_tool(tool: str) -> bool:
    canonical = tool.strip()
    lowered = canonical.lower()
    banned = {item.lower() for item in BANNED_REVIEW_TOOLS}
    return lowered in banned or any(lowered.startswith(f"{item}/") for item in banned)


def _valid_agents_value(value: object) -> bool:
    if value == "*":
        return True
    if isinstance(value, list):
        return all(isinstance(item, str) and item.strip() for item in value)
    return False
