from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ast


class CustomAgentValidationError(RuntimeError):
    """Raised when a VS Code Copilot custom agent definition is invalid."""


@dataclass(frozen=True)
class CustomAgentDefinition:
    path: Path
    name: str
    description: str
    tools: list[str]
    agents: list[str] | str | None
    instructions: str


BANNED_REVIEW_TOOLS = {
    "edit",
    "terminal",
    "runCommands",
    "runTask",
    "notebookEdit",
}


def validate_custom_agents(root: Path | None = None) -> list[CustomAgentDefinition]:
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


def _load_agent(path: Path) -> CustomAgentDefinition:
    text = path.read_text(encoding="utf-8")
    metadata, instructions = _split_frontmatter(text, path)

    name = metadata.get("name") or path.name.removesuffix(".agent.md")
    description = metadata.get("description")
    tools = metadata.get("tools", [])
    agents = metadata.get("agents")

    if not isinstance(name, str) or not name.strip():
        raise CustomAgentValidationError(f"{path}: name must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise CustomAgentValidationError(f"{path}: description must be a non-empty string")
    if not isinstance(tools, list) or not all(isinstance(item, str) and item.strip() for item in tools):
        raise CustomAgentValidationError(f"{path}: tools must be a list of non-empty strings")
    if any(tool in BANNED_REVIEW_TOOLS for tool in tools):
        raise CustomAgentValidationError(f"{path}: review agents must not enable editing or terminal tools")
    if "agent" in tools and agents is None:
        raise CustomAgentValidationError(f"{path}: agent tool requires an agents field")
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
    )


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise CustomAgentValidationError(f"{path}: YAML frontmatter is required")

    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise CustomAgentValidationError(f"{path}: YAML frontmatter is not closed") from exc

    metadata = _parse_simple_yaml(lines[1:end], path)
    instructions = "\n".join(lines[end + 1 :])
    return metadata, instructions


def _parse_simple_yaml(lines: list[str], path: Path) -> dict[str, object]:
    metadata: dict[str, object] = {}
    current_key: str | None = None

    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  - "):
            if current_key is None:
                raise CustomAgentValidationError(f"{path}: list item without a key")
            existing = metadata.setdefault(current_key, [])
            if not isinstance(existing, list):
                raise CustomAgentValidationError(f"{path}: mixed scalar/list value for {current_key}")
            existing.append(_parse_scalar(raw[4:].strip()))
            continue
        if raw.startswith(" "):
            raise CustomAgentValidationError(f"{path}: unsupported YAML indentation")
        if ":" not in raw:
            raise CustomAgentValidationError(f"{path}: invalid frontmatter line")

        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise CustomAgentValidationError(f"{path}: empty frontmatter key")
        current_key = key
        metadata[key] = [] if value == "" else _parse_scalar(value)

    return metadata


def _parse_scalar(value: str) -> object:
    if value in {"[]", "{}"} or value.startswith("[") or value.startswith("{"):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _valid_agents_value(value: object) -> bool:
    if value == "*":
        return True
    if isinstance(value, list):
        return all(isinstance(item, str) and item.strip() for item in value)
    return False
