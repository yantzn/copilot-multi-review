from __future__ import annotations

from pathlib import Path

import pytest

from ai_review.custom_agent_installer import (
    AGENT_FILENAMES,
    CustomAgentInstallError,
    install_agents,
    installed_filename,
    manifest_path,
    packaged_agents_dir,
    source_agents_dir,
    status_agents,
    sync_agents,
    uninstall_agents,
    user_agents_dir,
)


def _set_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "copilot-home"
    monkeypatch.setenv("COPILOT_MULTI_REVIEW_HOME", str(home))
    return home


def test_packaged_templates_match_workspace_agents() -> None:
    workspace_agents = Path(".github") / "agents"
    packaged = packaged_agents_dir()

    for name in AGENT_FILENAMES:
        assert (workspace_agents / name).read_text(encoding="utf-8") == (
            packaged / name
        ).read_text(encoding="utf-8")


def test_install_agents_copies_managed_agents_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)

    result = install_agents()

    assert result["total"] == len(AGENT_FILENAMES)
    assert len(result["installed"]) == len(AGENT_FILENAMES)
    assert manifest_path().exists()
    for name in AGENT_FILENAMES:
        assert (user_agents_dir() / installed_filename(name)).exists()


def test_status_detects_current_outdated_and_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    install_agents()

    first = AGENT_FILENAMES[0]
    second = AGENT_FILENAMES[1]
    (user_agents_dir() / installed_filename(first)).write_text(
        "modified",
        encoding="utf-8",
    )
    (user_agents_dir() / installed_filename(second)).unlink()

    statuses = {item.source_name: item.status for item in status_agents()}

    assert statuses[first] == "outdated"
    assert statuses[second] == "missing"
    assert all(
        status == "current"
        for name, status in statuses.items()
        if name not in {first, second}
    )


def test_sync_restores_outdated_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    install_agents()

    name = AGENT_FILENAMES[0]
    target = user_agents_dir() / installed_filename(name)
    target.write_text("stale", encoding="utf-8")

    result = sync_agents()

    assert installed_filename(name) in result["updated"]
    assert target.read_text(encoding="utf-8") == (
        source_agents_dir() / name
    ).read_text(encoding="utf-8")
    assert {item.status for item in status_agents()} == {"current"}


def test_uninstall_removes_only_manifest_managed_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    install_agents()
    unrelated = user_agents_dir() / "my-personal-reviewer.agent.md"
    unrelated.write_text("# personal agent\n", encoding="utf-8")

    result = uninstall_agents()

    assert result["total"] == len(AGENT_FILENAMES)
    assert unrelated.exists()
    assert not manifest_path().exists()
    for name in AGENT_FILENAMES:
        assert not (user_agents_dir() / installed_filename(name)).exists()


def test_install_refuses_unmanaged_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_home(monkeypatch, tmp_path)
    user_agents_dir().mkdir(parents=True)
    target = user_agents_dir() / installed_filename(AGENT_FILENAMES[0])
    target.write_text("# unmanaged\n", encoding="utf-8")

    with pytest.raises(CustomAgentInstallError):
        install_agents()
