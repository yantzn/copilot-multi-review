from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil


AGENT_FILENAMES = (
    "review-orchestrator.agent.md",
    "requirements-reviewer.agent.md",
    "correctness-reviewer.agent.md",
    "design-conformance-reviewer.agent.md",
    "project-rules-reviewer.agent.md",
    "security-reviewer.agent.md",
    "testing-reviewer.agent.md",
    "maintainability-reviewer.agent.md",
    "performance-reviewer.agent.md",
    "operations-reviewer.agent.md",
    "devil-advocate.agent.md",
    "final-reviewer.agent.md",
)

INSTALLED_PREFIX = "copilot-multi-review-"
MANIFEST_VERSION = 1


class CustomAgentInstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentStatus:
    source_name: str
    installed_name: str
    status: str
    source_sha256: str
    installed_sha256: str | None


def copilot_home() -> Path:
    override = os.environ.get("COPILOT_MULTI_REVIEW_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".copilot").resolve()


def user_agents_dir() -> Path:
    return copilot_home() / "agents"


def metadata_dir() -> Path:
    return copilot_home() / "copilot-multi-review"


def manifest_path() -> Path:
    return metadata_dir() / "agents-manifest.json"


def packaged_agents_dir() -> Path:
    return Path(__file__).resolve().parent / "agent_templates"


def checkout_agents_dir() -> Path:
    return Path(__file__).resolve().parents[1] / ".github" / "agents"


def source_agents_dir() -> Path:
    override = os.environ.get("COPILOT_MULTI_REVIEW_AGENT_SOURCE_DIR")
    if override:
        source = Path(override).expanduser().resolve()
        _validate_source_dir(source)
        return source

    checkout = checkout_agents_dir()
    if _contains_all_agents(checkout):
        return checkout

    packaged = packaged_agents_dir()
    _validate_source_dir(packaged)
    return packaged


def installed_filename(source_name: str) -> str:
    return f"{INSTALLED_PREFIX}{source_name}"


def install_agents() -> dict[str, object]:
    return sync_agents()


def sync_agents() -> dict[str, object]:
    source_dir = source_agents_dir()
    destination_dir = user_agents_dir()
    manifest = _load_manifest()
    managed_before = {
        str(item.get("installed_name"))
        for item in manifest.get("agents", [])
        if item.get("installed_name")
    }

    destination_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir().mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    records: list[dict[str, str]] = []

    for source_name in AGENT_FILENAMES:
        source = source_dir / source_name
        target_name = installed_filename(source_name)
        target = destination_dir / target_name
        source_hash = _sha256(source)

        if target.exists():
            target_hash = _sha256(target)
            if target_name not in managed_before:
                raise CustomAgentInstallError(
                    f"管理対象外の既存Custom Agentと衝突しています: {target}"
                )
            if target_hash == source_hash:
                unchanged.append(target_name)
            else:
                shutil.copyfile(source, target)
                updated.append(target_name)
        else:
            shutil.copyfile(source, target)
            installed.append(target_name)

        records.append(
            {
                "source_name": source_name,
                "installed_name": target_name,
                "sha256": source_hash,
            }
        )

    current_names = {item["installed_name"] for item in records}
    removed: list[str] = []
    for stale_name in sorted(managed_before - current_names):
        stale = destination_dir / stale_name
        if stale.exists():
            stale.unlink()
            removed.append(stale_name)

    _write_manifest(
        {
            "manifest_version": MANIFEST_VERSION,
            "source_dir": str(source_dir),
            "agents_dir": str(destination_dir),
            "agents": records,
        }
    )

    return {
        "source_dir": str(source_dir),
        "agents_dir": str(destination_dir),
        "installed": installed,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "total": len(records),
    }


def status_agents() -> list[AgentStatus]:
    source_dir = source_agents_dir()
    destination_dir = user_agents_dir()
    manifest = _load_manifest()
    managed = {
        str(item.get("installed_name"))
        for item in manifest.get("agents", [])
        if item.get("installed_name")
    }

    statuses: list[AgentStatus] = []
    for source_name in AGENT_FILENAMES:
        source = source_dir / source_name
        source_hash = _sha256(source)
        target_name = installed_filename(source_name)
        target = destination_dir / target_name

        if not target.exists():
            state = "missing"
            installed_hash = None
        else:
            installed_hash = _sha256(target)
            if target_name not in managed and not manifest:
                state = "unmanaged_conflict"
            elif installed_hash == source_hash:
                state = "current"
            else:
                state = "outdated"

        statuses.append(
            AgentStatus(
                source_name=source_name,
                installed_name=target_name,
                status=state,
                source_sha256=source_hash,
                installed_sha256=installed_hash,
            )
        )
    return statuses


def uninstall_agents() -> dict[str, object]:
    destination_dir = user_agents_dir()
    manifest = _load_manifest()
    records = manifest.get("agents", [])
    removed: list[str] = []

    for item in records:
        installed_name = item.get("installed_name")
        if not isinstance(installed_name, str):
            continue
        target = destination_dir / installed_name
        if target.exists():
            target.unlink()
            removed.append(installed_name)

    manifest_file = manifest_path()
    if manifest_file.exists():
        manifest_file.unlink()
    meta = metadata_dir()
    if meta.exists() and not any(meta.iterdir()):
        meta.rmdir()

    return {
        "agents_dir": str(destination_dir),
        "removed": removed,
        "total": len(removed),
    }


def manifest_snapshot() -> dict[str, object]:
    return _load_manifest()


def _contains_all_agents(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in AGENT_FILENAMES)


def _validate_source_dir(path: Path) -> None:
    if not path.is_dir():
        raise CustomAgentInstallError(f"Custom Agentのソースディレクトリがありません: {path}")
    missing = [name for name in AGENT_FILENAMES if not (path / name).is_file()]
    if missing:
        joined = ", ".join(missing)
        raise CustomAgentInstallError(f"Custom Agentテンプレートが不足しています: {joined}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest() -> dict[str, object]:
    path = manifest_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CustomAgentInstallError(f"Custom Agent manifestを読み込めません: {path}") from exc
    if not isinstance(raw, dict):
        raise CustomAgentInstallError(f"Custom Agent manifestの形式が不正です: {path}")
    return raw


def _write_manifest(payload: dict[str, object]) -> None:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def status_as_dicts() -> list[dict[str, object]]:
    return [asdict(item) for item in status_agents()]
