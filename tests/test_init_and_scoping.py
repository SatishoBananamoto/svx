"""Tests for svx init, project scoping, and hook pause/resume."""

import sys
import json
from datetime import datetime, timedelta, timezone

import pytest

from svx.cli import _find_svx_root, _any_target_in_svx_project, main
from svx.config import (
    is_disabled_by_env,
    is_paused,
    is_vibe_mode,
    load_config,
    load_project_config,
)
from svx.schemas import ParsedCommand, CommandCategory
from svx.session import get_session_path, record_file_read


class TestSvxInit:
    """Test _find_svx_root project scoping."""

    def test_find_root_with_svx_dir(self, tmp_path):
        """Finds .svx/ in the target directory."""
        (tmp_path / ".svx").mkdir()
        assert _find_svx_root(tmp_path) == tmp_path

    def test_find_root_walks_up(self, tmp_path):
        """Finds .svx/ in a parent directory."""
        (tmp_path / ".svx").mkdir()
        child = tmp_path / "src" / "module"
        child.mkdir(parents=True)
        assert _find_svx_root(child) == tmp_path

    def test_no_svx_returns_none(self, tmp_path):
        """Returns None when no .svx/ exists anywhere."""
        child = tmp_path / "src"
        child.mkdir()
        assert _find_svx_root(child) is None

    def test_find_root_with_file(self, tmp_path):
        """Works when path is a file, not a directory."""
        (tmp_path / ".svx").mkdir()
        f = tmp_path / "test.py"
        f.write_text("pass")
        assert _find_svx_root(f) == tmp_path


class TestProjectScoping:
    """Test _any_target_in_svx_project."""

    def test_target_in_svx_project(self, tmp_path):
        """Returns True when target is inside a .svx/ project."""
        (tmp_path / ".svx").mkdir()
        target = str(tmp_path / "src" / "main.py")
        cmd = ParsedCommand(
            raw=f"cat {target}",
            program="cat",
            targets=[target],
            category=CommandCategory.SHELL,
        )
        assert _any_target_in_svx_project([cmd]) is True

    def test_target_outside_svx_project(self, tmp_path):
        """Returns False when target is NOT inside a .svx/ project."""
        target = str(tmp_path / "random" / "file.py")
        cmd = ParsedCommand(
            raw=f"cat {target}",
            program="cat",
            targets=[target],
            category=CommandCategory.SHELL,
        )
        assert _any_target_in_svx_project([cmd]) is False

    def test_no_targets(self):
        """Returns False when command has no targets."""
        cmd = ParsedCommand(
            raw="echo hello",
            program="echo",
            targets=[],
            category=CommandCategory.SHELL,
        )
        assert _any_target_in_svx_project([cmd]) is False


class TestConfig:
    """Test config loading and vibe mode."""

    def test_default_is_vibe(self):
        """Default mode is vibe."""
        config = {"mode": "vibe"}
        assert is_vibe_mode(config) is True

    def test_strict_mode(self):
        """Strict mode is not vibe."""
        config = {"mode": "strict"}
        assert is_vibe_mode(config) is False

    def test_missing_mode_defaults_to_vibe(self):
        """Missing mode key defaults to vibe."""
        assert is_vibe_mode({}) is True

    def test_project_config_overrides_home_config(self, tmp_path, monkeypatch):
        """Project-local mode should override global mode inside that project."""
        home = tmp_path / "home"
        project = tmp_path / "project"
        home.mkdir()
        (home / ".svx.yaml").write_text("mode: strict\n")
        (project / ".svx").mkdir(parents=True)
        (project / ".svx" / "config.yaml").write_text("mode: vibe\n")

        monkeypatch.setenv("HOME", str(home))

        assert load_config(cwd=project)["mode"] == "vibe"

    def test_env_override_disables_hook(self, monkeypatch):
        """SVX_DISABLED=1 should be treated as an explicit bypass."""
        monkeypatch.setenv("SVX_DISABLED", "1")

        assert is_disabled_by_env() is True

    def test_pause_resume_cli_toggles_project_config(self, tmp_path, monkeypatch):
        """pause/resume should toggle a project-local paused flag."""
        (tmp_path / ".svx").mkdir()
        config_path = tmp_path / ".svx" / "config.yaml"
        config_path.write_text("mode: strict\n")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["svx", "pause"])
        main()

        paused = load_project_config(tmp_path)
        assert paused["mode"] == "strict"
        assert paused["paused"] is True
        assert is_paused(paused) is True

        monkeypatch.setattr(sys, "argv", ["svx", "resume"])
        main()

        resumed = load_project_config(tmp_path)
        assert resumed["mode"] == "strict"
        assert resumed["paused"] is False
        assert is_paused(resumed) is False

    def test_pause_requires_initialized_project(self, tmp_path, monkeypatch):
        """pause should not create .svx implicitly."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["svx", "pause"])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 1
        assert not (tmp_path / ".svx").exists()

    def test_session_prune_cli_removes_stale_entries(self, tmp_path, monkeypatch, capsys):
        """session-prune should remove stale .svx session entries."""
        (tmp_path / ".svx").mkdir()
        target = tmp_path / "settings.env"
        target.write_text("OLD=1\n")
        record_file_read(target, cwd=tmp_path)

        session_path = get_session_path(tmp_path)
        assert session_path is not None
        data = json.loads(session_path.read_text())
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        data["reads"][str(target)]["seen_at"] = old
        session_path.write_text(json.dumps(data))

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["svx", "session-prune", "--max-age-hours", "1"])
        main()

        output = capsys.readouterr().out
        assert "removed 1 stale read record(s)." in output

        data_after = json.loads(session_path.read_text())
        assert str(target) not in data_after.get("reads", {})

    def test_session_prune_fails_without_project(self, tmp_path, monkeypatch):
        """session-prune should require an initialized project."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["svx", "session-prune"])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 1
