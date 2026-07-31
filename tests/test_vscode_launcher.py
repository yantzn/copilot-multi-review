from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ai_review.vscode_launcher import collect_preview
from ai_review import vscode_launcher


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.decode("utf-8").strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("# sample\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")
    return path


def test_collect_preview_before_copilot_call(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")

    preview = collect_preview(str(repo), target="uncommitted", agents="security")

    assert preview.repository.root == repo.resolve()
    assert preview.agents == "security"
    assert preview.changed_file_count == 1
    assert preview.diff_line_count == 1
    assert preview.has_uncommitted is True
    assert preview.has_staged is False


def test_launch_json_contains_required_configurations() -> None:
    launch = json.loads(Path(".vscode/launch.json").read_text(encoding="utf-8"))
    names = {item["name"] for item in launch["configurations"]}

    assert "compounds" not in launch
    assert {
        "Copilotレビュー：全エージェント",
        "Copilotレビュー：未コミット差分",
        "Copilotレビュー：ステージ済み差分",
        "Copilotレビュー：Security",
        "Copilotレビュー：前回条件で再実行",
        "Copilotレビュー：前回結果を表示",
        "Copilotレビュー：実行停止",
        "Copilotレビュー：設定検証",
    } <= names


def test_launch_config_sets_utf8_env() -> None:
    launch = json.loads(Path(".vscode/launch.json").read_text(encoding="utf-8"))
    for item in launch["configurations"]:
        assert item["type"] == "debugpy"
        assert item["console"] == "integratedTerminal"
        assert item["env"]["PYTHONUTF8"] == "1"
        assert item["env"]["PYTHONIOENCODING"] == "utf-8"


def test_tasks_json_has_status_task() -> None:
    tasks = json.loads(Path(".vscode/tasks.json").read_text(encoding="utf-8"))
    status_task = next(item for item in tasks["tasks"] if item["label"] == "Copilotレビュー: 実行状況を表示")

    assert status_task["type"] == "process"
    assert status_task["args"] == ["-m", "ai_review", "status"]
    assert status_task["options"]["env"]["PYTHONUTF8"] == "1"


def test_select_repository_keyboard_interrupt_is_cancel(monkeypatch, capsys) -> None:
    class FakeRoot:
        destroyed = False

        def withdraw(self) -> None:
            pass

        def attributes(self, *_args) -> None:
            pass

        def destroy(self) -> None:
            self.destroyed = True

    root = FakeRoot()
    monkeypatch.setattr(vscode_launcher.tk, "Tk", lambda: root)

    def raise_interrupt(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(vscode_launcher.filedialog, "askdirectory", raise_interrupt)

    assert vscode_launcher._select_repository() is None
    assert root.destroyed is True
    assert "キャンセル" in capsys.readouterr().out
