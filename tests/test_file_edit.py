"""Tests for file edit/write simulation — the full pipeline."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from svx.schemas import (
    CommandCategory,
    ParsedCommand,
    Reversibility,
    RiskLevel,
    Verdict,
    WorldSnapshot,
)
from svx.simulator import simulate
from svx.snapshot import capture
from svx.verifier import verify
from svx.cli import _parse_edit_tool, _parse_write_tool


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_edit_cmd(file_path: str, old_string: str, new_string: str) -> ParsedCommand:
    """Create an Edit ParsedCommand."""
    return ParsedCommand(
        raw=f"Edit {file_path}",
        program="Edit",
        category=CommandCategory.FILE_EDIT,
        targets=[file_path],
        metadata={"old_string": old_string, "new_string": new_string},
    )


def _make_write_cmd(file_path: str, content_length: int) -> ParsedCommand:
    """Create a Write ParsedCommand."""
    return ParsedCommand(
        raw=f"Write {file_path}",
        program="Write",
        category=CommandCategory.FILE_WRITE,
        targets=[file_path],
        metadata={"content_length": content_length},
    )


def _snap_for_edit(
    target: str,
    exists: bool = True,
    tracked: bool = True,
    is_config: bool = False,
    line_count: int = 50,
    size: int = 1000,
    old_string_found: bool = True,
    change_ratio: float = 0.1,
) -> WorldSnapshot:
    """Build a mock WorldSnapshot for edit tests."""
    return WorldSnapshot(
        cwd="/tmp/test",
        is_git_repo=True,
        git_branch="main",
        git_dirty=False,
        target_exists={target: exists},
        target_sizes={target: size},
        target_git_tracked={target: tracked},
        target_line_count={target: line_count},
        target_is_config={target: is_config},
        edit_old_string_found=old_string_found,
        edit_change_ratio=change_ratio,
    )


def _snap_for_write(
    target: str,
    exists: bool = False,
    tracked: bool = False,
    is_config: bool = False,
    line_count: int = 0,
    size: int = 0,
) -> WorldSnapshot:
    """Build a mock WorldSnapshot for write tests."""
    return WorldSnapshot(
        cwd="/tmp/test",
        is_git_repo=True,
        git_branch="main",
        git_dirty=False,
        target_exists={target: exists},
        target_sizes={target: size},
        target_git_tracked={target: tracked},
        target_line_count={target: line_count},
        target_is_config={target: is_config},
    )


# ── Edit tool tests ─────────────────────────────────────────────────────────


def test_small_edit_tracked_file_allows():
    """A small edit to a tracked file should ALLOW."""
    cmd = _make_edit_cmd("src/main.py", "old_code", "new_code")
    snap = _snap_for_edit("src/main.py", tracked=True, change_ratio=0.05)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert result.verdict == Verdict.ALLOW
    assert result.risk_level in (RiskLevel.NONE, RiskLevel.LOW)
    assert sim.reversibility == Reversibility.REVERSIBLE


def test_large_rewrite_confirms():
    """Replacing >50% of a file should require confirmation."""
    cmd = _make_edit_cmd("src/main.py", "a" * 500, "b" * 800)
    snap = _snap_for_edit("src/main.py", tracked=True, change_ratio=0.6)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert result.verdict == Verdict.CONFIRM
    assert any("rewrite" in r.lower() or "60%" in r for r in result.reasons)


def test_config_file_edit_confirms():
    """Editing a config file should require confirmation."""
    cmd = _make_edit_cmd("pyproject.toml", "old", "new")
    snap = _snap_for_edit("pyproject.toml", tracked=True, is_config=True, change_ratio=0.05)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert result.verdict == Verdict.CONFIRM
    assert any("config" in r.lower() for r in result.reasons)


def test_edit_nonexistent_file():
    """Editing a file that doesn't exist should report it."""
    cmd = _make_edit_cmd("ghost.py", "old", "new")
    snap = _snap_for_edit("ghost.py", exists=False)
    sim = simulate(cmd, snap)

    assert "does not exist" in sim.description
    assert sim.blast_radius == 0


def test_edit_old_string_not_found():
    """When old_string isn't in the file, flag it."""
    cmd = _make_edit_cmd("src/main.py", "nonexistent_code", "new_code")
    snap = _snap_for_edit("src/main.py", old_string_found=False, change_ratio=0.1)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert any("not found" in s.lower() for s in result.suggestions)


def test_edit_untracked_file_warns_data_loss():
    """Editing an untracked file should flag potential data loss."""
    cmd = _make_edit_cmd("notes.txt", "old", "new")
    snap = _snap_for_edit("notes.txt", tracked=False, change_ratio=0.1)
    sim = simulate(cmd, snap)

    assert sim.data_loss_possible is True
    assert sim.reversibility == Reversibility.PARTIALLY


def test_edit_env_file_confirms():
    """.env is a config file — should require confirmation."""
    cmd = _make_edit_cmd(".env", "OLD_KEY=val", "NEW_KEY=val")
    snap = _snap_for_edit(".env", tracked=False, is_config=True, change_ratio=0.2)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert result.verdict == Verdict.CONFIRM
    assert any("config" in r.lower() for r in result.reasons)


def test_edit_gitignore_allows():
    """Editing .gitignore should not require confirmation by default."""
    cmd = _make_edit_cmd(".gitignore", "old", "new")
    snap = _snap_for_edit(".gitignore", tracked=True, is_config=False, change_ratio=0.02)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert result.verdict == Verdict.ALLOW
    assert all("config" not in reason.lower() for reason in result.reasons)


# ── Write tool tests ────────────────────────────────────────────────────────


def test_write_new_file_allows():
    """Creating a new file via Write should ALLOW."""
    cmd = _make_write_cmd("new_file.py", content_length=200)
    snap = _snap_for_write("new_file.py", exists=False)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert result.verdict == Verdict.ALLOW
    assert sim.reversibility == Reversibility.REVERSIBLE
    assert sim.data_loss_possible is False


def test_write_overwrite_tracked_allows():
    """Overwriting a tracked file via Write is recoverable — ALLOW."""
    cmd = _make_write_cmd("src/main.py", content_length=500)
    snap = _snap_for_write("src/main.py", exists=True, tracked=True, size=400, line_count=20)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert result.verdict == Verdict.ALLOW
    assert sim.reversibility == Reversibility.REVERSIBLE


def test_write_overwrite_untracked_confirms():
    """Overwriting an untracked file via Write — data loss, should CONFIRM."""
    cmd = _make_write_cmd("data/output.csv", content_length=100)
    snap = _snap_for_write("data/output.csv", exists=True, tracked=False, size=50000, line_count=1000)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert result.verdict == Verdict.CONFIRM
    assert sim.data_loss_possible is True
    assert sim.reversibility == Reversibility.IRREVERSIBLE
    assert any("untracked" in r.lower() for r in result.reasons)


def test_write_config_file_confirms():
    """Writing to a config file should CONFIRM."""
    cmd = _make_write_cmd("Dockerfile", content_length=300)
    snap = _snap_for_write("Dockerfile", exists=True, tracked=True, is_config=True, size=200, line_count=15)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert result.verdict == Verdict.CONFIRM
    assert any("config" in r.lower() for r in result.reasons)


def test_write_overwrite_suggestions():
    """Write overwriting existing file should suggest Edit instead."""
    cmd = _make_write_cmd("src/util.py", content_length=200)
    snap = _snap_for_write("src/util.py", exists=True, tracked=True, size=180, line_count=10)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)

    assert any("Edit" in s for s in result.suggestions)


# ── CLI parsing tests ───────────────────────────────────────────────────────


def test_parse_edit_tool():
    """_parse_edit_tool should produce a FILE_EDIT command."""
    cmd = _parse_edit_tool({
        "file_path": "/home/user/project/main.py",
        "old_string": "def foo():",
        "new_string": "def bar():",
    })

    assert cmd.category == CommandCategory.FILE_EDIT
    assert cmd.program == "Edit"
    assert cmd.targets == ["/home/user/project/main.py"]
    assert cmd.metadata["old_string"] == "def foo():"
    assert cmd.metadata["new_string"] == "def bar():"


def test_parse_write_tool():
    """_parse_write_tool should produce a FILE_WRITE command."""
    cmd = _parse_write_tool({
        "file_path": "/home/user/project/new.py",
        "content": "print('hello')",
    })

    assert cmd.category == CommandCategory.FILE_WRITE
    assert cmd.program == "Write"
    assert cmd.targets == ["/home/user/project/new.py"]
    assert cmd.metadata["content_length"] == 14


# ── Integration: snapshot with real files ────────────────────────────────────


def test_snapshot_reads_real_file_for_edit():
    """Snapshot should read actual file content to check old_string."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def hello():\n    print('world')\n")
        f.flush()
        tmp_path = f.name

    try:
        cmd = _make_edit_cmd(tmp_path, "print('world')", "print('universe')")
        snap = capture(cmd, cwd=os.path.dirname(tmp_path))

        assert snap.target_exists[tmp_path] is True
        assert snap.edit_old_string_found is True
        assert snap.target_line_count[tmp_path] == 2
        assert snap.edit_change_ratio > 0
    finally:
        os.unlink(tmp_path)


def test_snapshot_detects_missing_old_string():
    """Snapshot should detect when old_string is not in the file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def hello():\n    pass\n")
        f.flush()
        tmp_path = f.name

    try:
        cmd = _make_edit_cmd(tmp_path, "this_does_not_exist", "replacement")
        snap = capture(cmd, cwd=os.path.dirname(tmp_path))

        assert snap.edit_old_string_found is False
    finally:
        os.unlink(tmp_path)


def test_snapshot_detects_config_file():
    """Snapshot should flag config files and not flag .gitignore."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", prefix="pyproject", delete=False, dir="/tmp"
    ) as f:
        f.write('[project]\nname = "test"\n')
        f.flush()
        tmp_path = f.name

    try:
        cmd = _make_edit_cmd("pyproject.toml", "test", "new")
        snap = _snap_for_edit("pyproject.toml", is_config=True)
        assert snap.target_is_config["pyproject.toml"] is True

        with tempfile.TemporaryDirectory() as td:
            gitignore = Path(td) / ".gitignore"
            gitignore.write_text("*.pyc\n")
            cmd = _make_edit_cmd(str(gitignore), "*.pyc", "*.swp")
            real_snap = capture(cmd, cwd=td)
            assert real_snap.target_is_config.get(str(gitignore), False) is False
    finally:
        os.unlink(tmp_path)


# ── Hook integration tests ──────────────────────────────────────────────────


def test_hook_edit_flow():
    """Full hook flow for an Edit tool call — small edit to tracked file."""
    hook_input = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "src/main.py",
            "old_string": "old_code",
            "new_string": "new_code",
        },
        "hook_event_name": "PreToolUse",
    }

    cmd = _parse_edit_tool(hook_input["tool_input"])
    assert cmd.category == CommandCategory.FILE_EDIT

    # With a benign mock snapshot, should allow
    snap = _snap_for_edit("src/main.py", tracked=True, change_ratio=0.05)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.ALLOW


def test_hook_write_flow():
    """Full hook flow for a Write tool call — new file creation."""
    hook_input = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "new_module.py",
            "content": "# new module\n",
        },
        "hook_event_name": "PreToolUse",
    }

    cmd = _parse_write_tool(hook_input["tool_input"])
    assert cmd.category == CommandCategory.FILE_WRITE

    snap = _snap_for_write("new_module.py", exists=False)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.ALLOW
