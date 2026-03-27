"""Tests for svx init, project scoping, and vibe mode."""

import os
from pathlib import Path

import pytest

from svx.cli import _find_svx_root, _any_target_in_svx_project
from svx.config import load_config, is_vibe_mode
from svx.schemas import ParsedCommand, CommandCategory


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
